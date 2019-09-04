#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: lingquan
"""
from msppy.utils.statistics import (rand_int,check_random_state,compute_CI,
allocate_jobs)
import pandas
import time
import numpy
import multiprocessing


class _Evaluation(object):
    """Evaluation base class.

    Parameters
    ----------
    MSP: list
        A multi-stage stochastic program object.

    Attributes
    ----------
    db: list
        The deterministic bounds.

    pv: list
        The simulated policy values.

    epv: float
        The exact value of expected policy value (only available for
        approximation model).

    CI: tuple
        The CI of simulated policy values.

    gap: float
        The gap between upper end of the CI and deterministic bound.

    stage_cost: dataframe
        The cost of individual stage models.

    solution: dataframe
        The solution of queried variables.

    n_sample_paths: int
        The number of sample paths to evaluate policy.

    sample_paths_idx: list
        The index list of exhaustive sample paths if simulation is turned off.

    markovian_samples:
        The simulated Markovian type samples.

    markovian_idx: list
        The Markov state that is the closest to the markovian_samples.

    Methods
    -------
    run:
        Run simulations on the approximation model.
    """
    def __init__(self, MSP):
        self.MSP = MSP
        self.db = MSP.db
        self.pv = None
        self.CI = None
        self.epv = None
        self.gap = None
        self.stage_cost = None
        self.solution = None
        self.n_sample_paths = None
        self.sample_path_idx = None
        self.markovian_idx = None
        self.markovian_samples = None
        self.solve_true = False

    def _compute_gap(self):
        try:
            MSP = self.MSP
            if self.CI is not None:
                if MSP.sense == 1:
                    self.gap = abs( (self.CI[1]-self.db) / self.db )
                else:
                    self.gap = abs( (self.db-self.CI[0]) / self.db )
            elif self.epv is not None:
                self.gap = abs( (self.epv-self.db) / self.db )
            else:
                self.gap = abs( (self.pv[0]-self.db) / self.db )
        except ZeroDivisionError:
            self.gap = 'nan'

    def _compute_sample_path_idx_and_markovian_path(self):
        pass

    def run(
            self,
            n_simulations,
            percentile=95,
            query=None,
            query_dual=None,
            query_stage_cost=False,
            n_periodical_stages=None,
            n_processes=1,):
        """Run a Monte Carlo simulation to evaluate the policy.

        Parameters
        ----------
        n_simulations: int/-1
            If int: the number of simulations;
            If -1: exhuastive evaluation.

        query: list, optional (default=None)
            The names of variables that are intended to query.

        query_dual: list, optional (default=None)
            The names of constraints whose dual variables are intended to query.

        query_stage_cost: bool, optional (default=False)
            Whether to query values of individual stage costs.

        percentile: float, optional (default=95)
            The percentile used to compute the confidence interval.

        random_state: int, optional (default=None)
            the seed used by the random number
        """

        if n_processes != 1 and (query is not None or query_dual is not None
            or query_stage_cost): raise NotImplementedError
        from msppy.solver import SDDP, SDDP_infinity
        MSP = self.MSP
        if MSP.n_periodical_stages is not None:
            if n_periodical_stages is not None:
                MSP.n_periodical_stages = n_periodical_stages
            self.solver = SDDP_infinity(MSP, reset=False)
            T = MSP.n_periodical_stages
        else:
            self.solver = SDDP(MSP, reset=False)
            T = MSP.T
        self.n_simulations = n_simulations
        query = [] if query is None else list(query)
        query_dual = [] if query_dual is None else list(query_dual)
        query_stage_cost = query_stage_cost
        self._compute_sample_path_idx_and_markovian_path()
        self.pv = numpy.zeros(self.n_sample_paths)
        self.stage_cost = numpy.zeros((T,self.n_sample_paths))
        self.solution = {
            item: numpy.full((T,self.n_sample_paths), numpy.nan)
            for item in query
        }
        self.solution_dual = {
            item: numpy.full((T,self.n_sample_paths), numpy.nan)
            for item in query_dual
        }
        self.query = query
        self.query_dual = query_dual
        self.query_stage_cost = query_stage_cost
        if n_processes != 1:
            jobs = allocate_jobs(self.n_sample_paths, n_processes)
            pv = multiprocessing.Array("d", [0] * self.n_sample_paths)
            procs = [None] * n_processes
            for p in range(n_processes):
                procs[p] = multiprocessing.Process(
                    target=self.run_single,
                    args=(pv, jobs[p])
                )
                procs[p].start()
            for proc in procs:
                proc.join()
            self.pv = [item for item in pv]
        else:
            self.pv = [0] * self.n_sample_paths
            self.run_single(self.pv, range(self.n_sample_paths))
        if self.n_simulations == -1:
            self.epv = numpy.dot(
                pv,
                [
                    MSP._compute_weight_sample_path(self.sample_path_idx[j])
                    for j in range(self.n_sample_paths)
                ],
            )
        if self.n_simulations not in [-1,1]:
            self.CI = compute_CI(self.pv, percentile)
        self._compute_gap()
        self.solution = {k: pandas.DataFrame(v) for k, v in self.solution.items()}
        self.solution_dual = {k: pandas.DataFrame(v) for k, v in self.solution_dual.items()}
        if query_stage_cost:
            self.stage_cost = pandas.DataFrame(self.stage_cost)

    def run_single(self, pv, jobs):
        random_state = numpy.random.RandomState([2**32-1, jobs[0]])
        for j in jobs:
            print(j)
            sample_path_idx = (self.sample_path_idx[j]
                if self.sample_path_idx is not None else None)
            markovian_idx = (self.markovian_idx[j]
                if self.markovian_idx is not None else None)
            markovian_samples = (self.markovian_samples[j]
                if self.markovian_samples is not None else None)
            result = self.solver._forward(
                random_state=random_state,
                sample_path_idx=sample_path_idx,
                markovian_idx=markovian_idx,
                markovian_samples=markovian_samples,
                solve_true=self.solve_true,
                query=self.query,
                query_dual=self.query_dual,
                query_stage_cost=self.query_stage_cost
            )
            for item in self.query:
                self.solution[item][:,j] = result['solution'][item]
            for item in self.query_dual:
                self.solution_dual[item][:,j] = result['solution_dual'][item]
            if self.query_stage_cost:
                self.stage_cost[:,j] = result['stage_cost']
            pv[j] = result['pv']

class Evaluation(_Evaluation):
    __doc__ = _Evaluation.__doc__
    def _compute_sample_path_idx_and_markovian_path(self):
        if self.n_simulations == -1:
            self.n_sample_paths,self.sample_path_idx = self.MSP._enumerate_sample_paths(self.MSP.T-1)
        else:
            self.n_sample_paths = self.n_simulations


class EvaluationTrue(Evaluation):
    __doc__ = Evaluation.__doc__
    def run(self, *args, **kwargs):
        MSP = self.MSP
        if MSP.__class__.__name__ == 'MSIP':
            MSP._back_binarize()
        # discrete finite model should call evaluate instead
        if (
            MSP._type in ["stage-wise independent", "Markov chain"]
            and MSP._individual_type == "original"
            and not hasattr(MSP,"bin_stage")
        ):
            return super().run(*args, **kwargs)
        return _Evaluation.run(self, *args, **kwargs)

    def _compute_sample_path_idx_and_markovian_path(self):
        MSP = self.MSP
        self.n_sample_paths = self.n_simulations
        self.solve_true = True
        if MSP._type == "Markovian":
            self.markovian_samples = MSP.Markovian_uncertainty(
                self.random_state,self.n_simulations)
            self.markovian_idx = numpy.zeros([self.n_simulations,MSP.T],dtype=int)
            for t in range(1,MSP.T):
                dist = numpy.empty([self.n_simulations,MSP.n_Markov_states[t]])
                for idx, markov_state in enumerate(MSP.Markov_states[t]):
                    temp = self.markovian_samples[:,t,:] - markov_state
                    dist[:,idx] = numpy.sum(temp**2, axis=1)
                self.markovian_idx[:,t] = numpy.argmin(dist,axis=1)
