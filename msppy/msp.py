#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: lingquan
"""
from msppy.sp import StochasticModel,StochasticModelLG
import gurobipy
from itertools import product
import numpy
import pandas
from msppy.utils.statistics import check_random_state
from msppy.utils.statistics import check_Markovian_uncertainty
from msppy.utils.statistics import check_Markov_states_and_transition_matrix
from msppy.utils.exception import MarkovianDimensionError
from collections import abc
import numbers
import math


class MSLP(object):
    """A multistage stochastic linear program.

    A multistage stochastic linear program is composed of a sequence of
    StochasticModels. It is stage-wise independent if no Markovian uncertainties
    are specified. Markov chain uncertainties should be specified by Markov
    state spaces and transition matrices. Markovian continuous uncertainties
    should be specified by a sample path generator.
    SAA,SA,RSA are the three methods to discrete Markovian continuous
    uncertainties.
    Extensive solver and SDDP solver are the two solvers to obtain policies from
    the approximation model.
    Evaluation of computed policies can be done both on the true problem and
    the approximation model.

    Parameters
    ----------
    T: integer
        The number of stages.

    bound: float, optional
        A known lower bound for minimization problem or a known upper bound
        for maximation problem. Default value is -1B for maximization problem
        and 1B for maximation problem.

    sense: +1/-1, optional (default=1)
        The optimization sense. +1 indicates minimization and -1 indicates
        maximization.

    discount: float between 0(exclusive) and 1(inclusive), optional (default=1)
        The discount factor used to compute present value.

    **kwargs: optional
        Gurobipy attributes to specify on individual StochasticModels. (e.g.,
        presolve, method)

    Attributes
    ----------
    db: float
        The deterministic bound

    Methods
    -------
    add_MC_uncertainty:
        Set Markov state spaces and transition matricies for the Markov chain
        process.

    add_Markovian_uncertainty:
        Set a sample path generator for the Markovian process.

    discretize:
        discretize the Markovian continuous process.
    """
    def __init__(
            self,
            T,
            bound=None,
            sense=1,
            outputFlag=0,
            discount=1.0,
            **kwargs):
        if (T < 2
                or discount > 1
                or discount < 0
                or sense not in [-1, 1]
                or outputFlag not in [0, 1]):
            raise Exception('Arguments of SDDP construction are not valid!')

        self.T = T
        self.discount = discount
        self.bound = bound
        self.sense = sense
        self.n_Markov_states = 1
        self.dim_Markov_states = {}
        self.measure = 'risk neutral'
        self._type = 'stage-wise independent'
        self._individual_type = 'original'
        self._set_up_default_bound()
        self._set_up_model()
        self._set_up_model_attr(sense, outputFlag, kwargs)
        self._flag_discrete = 0
        self._flag_update = 0
        self.db = None

    def __repr__(self):
        sense = 'Minimization' if self.sense == 1 else 'Maximization'
        string = ("<SDDP instance {} {} {} problem, {} stages, "
            + "{} discount, {} known bound>")
        return string.format(sense, self.measure, self._type, self.T,
            self.discount, self.bound)

    def __getitem__(self, t):
        return self.models[t]

    def _set_up_default_bound(self):
        if self.bound is None:
            self.bound = -1000000000 if self.sense == 1 else 1000000000

    def _set_up_model(self):
        self.models = [StochasticModel(name=str(t)) for t in range(self.T)]

    def _set_up_model_attr(self, sense, outputFlag, kwargs):
        for t in range(self.T):
            m = self.models[t]
            m.Params.outputFlag = outputFlag
            m.setAttr('modelsense', sense)
            for k,v in kwargs.items():
                m.setParam(k,v)

    def add_MC_uncertainty(
            self,
            Markov_states,
            transition_matrix
        ):
        """Add a Markov chain process.

        Parameters
        ----------
        Markov_states: list of array-like
            Markov state spaces in each stage.

        transition_matrix: list of matrix-like
            Markov chain transition matrices in each stage.

        start: start period (inclusive) of the Markov chain process

        end: end period (exclusive) of the Markov chain process

        The dimension of all entries in Markov states and transition matrices
        must be in the form of:
            Markov_states: [1], [p_{1}], ... , [p_{T-1}]
            transition_matrix: [[1]], [1,p_{1}], [p_{1},p_{2}], [p_{T-2},p_{T-1}]
        where p_1,...p_{T-1} are integers.

        Examples
        --------
        Suppose there are three stages.

        add_MC_uncertainty(
            Markov_states=[
                [0.2],
                [0.3,0.5],
                [0.4,0.6]
            ],
            transition_matrix=[
                [[1]],
                [[0.2,0.8]],
                [[0.6,0.4],[0.3,0.7]]
            ]
        )
        """
        if hasattr(self, "Markovian_uncertainty") or hasattr(self,"Markov_states"):
            raise ValueError("Markovian uncertainty has already added!")
        info = check_Markov_states_and_transition_matrix(
            Markov_states, transition_matrix, self.T
        )
        self.dim_Markov_states,self.n_Markov_states = info
        self.Markov_states = Markov_states
        self.transition_matrix = transition_matrix
        self._type = 'Markov chain'

    def add_Markovian_uncertainty(self, Markovian_uncertainty):
        """Add a Markovian continuous process.

        Parameters
        ---------
        Markovian_uncertainty: callable
            A sample path generator. The callable should take
            numpy.random.randomState and size as its parameters.
            It should return a three dimensional numpy array
            (n_samples * T * n_states)

        Example
        -------
        Unidimensional:
            Consider an autoregressive model:
                X_t = 0.5 * X_{t-1} + \epsilon, where \epsilon ~ N(0,1)
            The stochastic process generator can be defined as:
                def f(random_state,size):
                    a = numpy.empty([size,T,1])
                    a[:,0,:] = 0.2
                    for t in range(1,T):
                        a.append(0.5 * a[-1] + random_state.normal(0,1))
                    return a

        Multidimensional：
            Consider an autoregressive model:
                X_t = 0.5 * X_{t-1} + \epsilon, where \epsilon ~ N(0,I_{2*2}))
            The stochastic process generator can be defined as:
                def f(random_state):
                    a = numpy.empty([size,T,1])
                    a[:,0,:] = numpy.array([[0.2,0.2]])
                    for t in range(T):
                        a.append(0.5 * a[-1] + random_state.normal(0,1))
                    return a
                            0.5 * numpy.array(a[-1])
                            + random_state.multivariate_normal(
                                mean = [0, 0],
                                cov = [[0, 1],[1,0]] )
                        )
                    return a
        """
        if hasattr(self, "Markovian_uncertainty") or hasattr(self,
        "Markov_states"):
            raise ValueError("Markovian uncertainty has already added!")
        self.dim_Markov_states=check_Markovian_uncertainty(Markovian_uncertainty
        ,self.T)
        self.Markovian_uncertainty = Markovian_uncertainty
        self._type = 'Markovian'

    def _check_multistage_model(self):
        """Check Markovian uncertainties are discretized. Copy StochasticModels
        for every Markov states."""
        if self._type == "Markovian" and self._flag_discrete == 0:
            raise Exception("Markovian uncertainties must be discretized!")
        if self._type == "Markov chain" or (
            self._type == "Markovian" and self._flag_discrete == 1
        ):
            if type(self.models[0]) != list:
                models = self.models
                self.models = [
                    [None for k in range(self.n_Markov_states[t])]
                    for t in range(self.T)
                ]
                for t in range(self.T):
                    m = models[t]
                    for k in range(self.n_Markov_states[t]):
                        m._update_uncertainty_dependent(self.Markov_states[t][k])
                        m.update()
                        self.models[t][k] = m.copy()

    def _check_inidividual_Markovian_index(self):
        """Check dimension indices of sample path generator are set properly."""
        for t in range(self.T):
            M = (
                self.models[t]
                if type(self.models[t]) == list
                else [self.models[t]]
            )
            for m in M:
                if m.Markovian_dim_index != []:
                    if any(index not in range(self.dim_Markov_states[t])
                            for index in m.Markovian_dim_index):
                        raise MarkovianDimensionError

    def _check_first_stage_model(self):
        """Ensure the first stage model is deterministic. The First stage model
        is only allowed to have uncertainty with length one."""
        m = self.models[0] if type(self.models[0]) != list else self.models[0][0]
        if m.n_samples != 1:
            raise Exception("First stage must be deterministic!")
        else:
            m._update_uncertainty(0)
            m.update()

    def _check_individual_stage_models(self):
        """Check state variables are set properly. Check stage-wise continuous
        uncertainties are discretized."""
        m = self.models[0] if type(self.models[0]) != list else self.models[0][0]
        if m.states == []:
            raise Exception("State variables must be set!")
        n_states = m.n_states
        for t in range(1, self.T):
            M = (
                self.models[t]
                if type(self.models[t]) == list
                else [self.models[t]]
            )
            for m in M:
                if m._type == "continuous":
                    if m._flag_discrete == 0:
                        raise Exception(
                            "Stage-wise independent continuous uncertainties "+
                            "must be discretized!"
                        )
                    self._individual_type = "discretized"
                else:
                    if m._flag_discrete == 1:
                        self._individual_type = "discretized"
                if m.n_states != n_states:
                    raise Exception(
                        "Dimension of state space must be same for all stages!"
                    )
        if self._type == "Markovian" and self._flag_discrete == 0:
            raise Exception(
                "Stage-wise dependent continuous uncertainties "+
                "must be discretized!"
            )
        self.n_states = [n_states] * self.T

    def _reset(self):
        """Reset the program to its original state."""
        for t in range(self.T):
            M = (
                self.models[t]
                if type(self.models[t]) == list
                else [self.models[t]]
            )
            for m in M:
                m._reset()

    def discretize(
            self,
            n_samples=None,
            random_state=None,
            replace=True,
            n_Markov_states=None,
            method='SA',
            n_sample_paths=None,
            Markov_states=None,
            transition_matrix=None,
            int_flag=0):
        """Discretize Markovian continuous uncertainty by k-means or (robust)
        stochasitic approximation.

        Parameter
        ---------
        n_samples: int | None (default=None)
            number of i.i.d. samples to generate for stage-wise independent
            randomness.

        random_state: None | int | instance of RandomState, optional (default=None)
            If int, random_state is the seed used by the
            random number generator;
            If RandomState instance, random_state is the
            random number generator;
            If None, the random number generator is the
            RandomState instance used by numpy.random.

        replace: bool (default=True)
            Indicates generating i.i.d. samples with/without replacement for
            stage-wise independent randomness.

        n_Markov_states: list | int | None
            If list, it specifies different dimensions of Markov state space
            over time. Length of the list should equal length of the Markovian
            uncertainty.
            If int, it specifies dimensions of Markov state space.

        Note: If the uncertainties are int, trained Markov states will be
        rounded to integers, and duplicates will be removed. In such cases,
        there is no guaranttee that the number of Markov states is n_Markov_states.

        method: binary, optional (default=0)
            'input': the approximating Markov chain is given by user input (
            through specifying Markov_states and transition_matrix)
            'SAA': use k-means to train Markov chain.
            'SA': use stochastic approximation to train Markov chain.
            'RSA': use robust stochastic approximation to train Markov chain.

        n_sample_paths: int | None  (default=None)
            number of sample paths to train the Markov chain.

        Markov_states/transition_matrix: array-like (default=None)
            Use input of approximating Markov chain. Length of the array-like
            should be T. Each entry of Markov_states should be unidimensional
            array-like. Each entry of transition_matrix should be bidimensional
            array-like.

            Note: the first stage model is always deterministic. Therefore, the
            first entry of Markov_states should be of length one. The first
            entry of transition_matrix should be [[1]].

        """
        if n_samples is not None:
            if isinstance(n_samples, (numbers.Integral, numpy.integer)):
                if n_samples < 1:
                    raise ValueError("n_samples should be bigger than zero!")
                n_samples = (
                    [1]
                    +[n_samples] * (self.T-1)
                )
            elif isinstance(n_samples, (abc.Sequence, numpy.ndarray)):
                if len(n_samples) != self.T:
                    raise ValueError(
                        "n_samples list should be of length {} rather than {}!"
                        .format(self.T,len(n_samples))
                    )
                if n_samples[0] != 1:
                    raise ValueError(
                        "The first stage model should be deterministic!"
                    )
            else:
                raise ValueError("Invalid input of n_samples!")
            # discretize stage-wise independent continuous distribution
            random_state = check_random_state(random_state)
            for t in range(1,self.T):
                self.models[t]._discretize(n_samples[t],random_state,replace)
        if n_Markov_states is None: return
        if n_Markov_states is not None:
            if isinstance(n_Markov_states, (numbers.Integral, numpy.integer)):
                if n_Markov_states < 1:
                    raise ValueError("n_Markov_states should be bigger than zero!")
                n_Markov_states = (
                    [1]
                    +[n_Markov_states] * (self.T-1)
                )
            elif isinstance(n_Markov_states, (abc.Sequence, numpy.ndarray)):
                if len(n_Markov_states) != self.T:
                    raise ValueError(
                        "n_Markov_states list should be of length {} rather than {}!"
                        .format(self.T,len(n_Markov_states))
                    )
                if n_Markov_states[0] != 1:
                    raise ValueError(
                        "The first stage model should be deterministic!"
                    )
            else:
                raise ValueError("Invalid input of n_Markov_states!")
        from msppy.discretize import Markovian
        if method in ['RSA','SA','SAA']:
            markovian = Markovian(
                f=self.Markovian_uncertainty,
                n_Markov_states=n_Markov_states,
                n_sample_paths=n_sample_paths,
                int_flag=int_flag,
            )
        if method in ['RSA','SA','SAA']:
            self.Markov_states,self.transition_matrix = getattr(markovian, method)()
        elif method == 'input':
            dim_Markov_states, n_Markov_states = (
                check_Markov_states_and_transition_matrix(
                    Markov_states=Markov_states,
                    transition_matrix=transition_matrix,
                    T=self.T,
                )
            )
            if dim_Markov_states != self.dim_Markov_states:
                raise ValueError("The dimension of the given sample path "
                    +"generator is not the same as the given Markov chain "
                    +"approximation!")
            self.Markov_states = Markov_states
            self.transition_matrix = transition_matrix
        self._flag_discrete = 1
        self.n_Markov_states = n_Markov_states
        if method in ['RSA','SA','SAA']:
            return markovian

    def write(self, path, suffix):
        """Write all StochasticModels to files.
        If stage-wise independent, the files would be named as
        stage_t (t is the stage)
        If Markov chain, the files would be named as stage_t_k (t is the stage
        and k is the index of Markov state)

        Parameters
        ----------
        path: string
            The location to write the StochasticModel

        suffix: string
            The format to write the StochasticModel

        examples
        --------
        write(path = "/Users/lingquan/Desktop", suffix = ".lp")
        """
        for t in range(self.T):
            m = self.models[t]
            if type(m) != list:
                m.write(path + "/stage_{}{}".format(t, suffix))
            else:
                for k, m in enumerate(m):
                    m.write(path + "/stage_{}_{}{}".format(t, k, suffix))

    def write_cuts(self, path):
        """Write all cuts to csv files.
        csv files takes the form of:
            x.varName | y.varName | rhs
            a         | b         | c
        which specifies cut:
            alpha + ax + by >= c in minimization problem
            alpha + ax + by <= c in maximization problem

        Parameters
        ----------
        path: string
            The location to write csv files
        """
        for t in range(self.T - 1):
            m = self.models[t]
            if type(m) != list:
                pandas.DataFrame(
                    m.get_cut_coeffs_and_rhs(),
                    columns=[state.varName for state in m.states] + ["rhs"],
                ).to_csv(path + "{}.csv".format(t))
            else:
                for k, m in enumerate(m):
                    pandas.DataFrame(
                        m.get_cut_coeffs_and_rhs(),
                        columns=[state.varName for state in m.states]
                        + ["rhs"],
                    ).to_csv(path + "{}_{}.csv".format(t, k))

    def read_cuts(self, path):
        """Read all cuts from csv files.
        csv files takes the form of:
            x.varName | y.varName | rhs
            a         | b         | c
        which specifies cut:
            alpha + ax + by >= c in minimization problem
            alpha + ax + by <= c in maximization problem

        Parameters
        ----------
        path: string
            The location to read csv files
        """
        self._update()
        for t in range(self.T - 1):
            m = self.models[t]
            if type(m) != list:
                coeffs = pandas.read_csv(
                    path + "{}.csv".format(t), index_col=0
                ).values
                for coeff in coeffs:
                    m.addConstr(
                        (
                            m.alpha
                            + gurobipy.LinExpr(coeff[:-1], m.states)
                            - coeff[-1]
                        )
                        * m.modelsense
                        >= 0
                    )
                    m.update()
            else:
                for k, m in enumerate(m):
                    coeffs = pandas.read_csv(
                        path + "{}_{}.csv".format(t, k), index_col=0
                    ).values
                    for coeff in coeffs:
                        m.addConstr(
                            (
                                m.alpha
                                + gurobipy.LinExpr(coeff[:-1], m.states)
                                - coeff[-1]
                            )
                            * m.modelsense
                            >= 0
                        )
                        m.update()

    def _set_up_CTG(self):
        for t in range(self.T):
            if t != self.T - 1:
                # MC model may already do model copies
                M = (
                    [self.models[t]]
                    if type(self.models[t]) != list
                    else self.models[t]
                )
                for m in M:
                    m._set_up_CTG(discount=self.discount, bound=self.bound)
                    m.update()

    def _get_stage_cost(self, m, t):
        if self.measure == "risk neutral":
            # the last stage model does not contain the cost-to-go function
            if t != self.T-1:
                return pow(self.discount,t) * (
                    m.objVal - self.discount*m.alpha.X
                )
            else:
                return pow(self.discount,t) * m.objVal
        else:
            return pow(self.discount,t) * m.getVarByName("stage_cost").X

    def _set_up_link_constrs(self):
        # model copies may not be ready while state size may have changed
        for t in range(1, self.T):
            M = (
                self.models[t]
                if type(self.models[t]) == list
                else [self.models[t]]
            )
            for m in M:
                m._set_up_link_constrs()
                m.update()

    def _delete_link_constrs(self):
        # model copies may not be ready while state size may have changed
        for t in range(1, self.T):
            M = (
                self.models[t]
                if type(self.models[t]) == list
                else [self.models[t]]
            )
            for m in M:
                m._delete_link_constrs()
                m.update()

    def set_AVaR(self, lambda_, alpha_):
        """Set linear combination of expectation and conditional value at risk
        (average value at risk) as risk measure

        Parameters
        ----------
        lambda_: float between 0 and 1/array-like of floats between 0 and 1
            The weight of AVaR: \lambda_2,\dots,\lambda_T
            If float, \lambda_2,\dots,\lambda_T will be assigned to the same
            value.
            If array-like, must be of length T-1.

        alpha_: float between 0 and 1/array-like of floats between 0 and 1
            The quantile parameter in value-at-risk: \alpha_2,\dots,\alpha_T
            If float, \alpha_2,\dots,\alpha_T will be assigned to the same
            value.
            If array-like, must be of length T-1.
        Remark
        ------
            Bigger lambda_ means more risk averse;
            smaller alpha_  means more risk averse.
        """
        if isinstance(lambda_, (abc.Sequence, numpy.ndarray)):
            if len(lambda_) != self.T-1:
                raise ValueError("Length of lambda_ must be T-1!")
            if not all(item <= 1 and item >= 0 for item in lambda_):
                raise ValueError("lambda_ must be between 0 and 1!")
            lambda_ = [None] + list(lambda_)
        elif isinstance(lambda_, (numbers.Number)):
            if lambda_ > 1 or lambda_ < 0:
                raise ValueError("lambda_ must be between 0 and 1!")
            lambda_ = [None] + [lambda_] * (self.T-1)
        else:
            raise TypeError("lambda_ should be float/array-like instead of \
            {}!".format(type(lambda_)))
        if isinstance(alpha_, (abc.Sequence, numpy.ndarray)):
            if len(alpha_) != self.T-1:
                raise ValueError("Length of alpha_ must be T-1!")
            if not all(item <= 1 and item >= 0 for item in alpha_):
                raise ValueError("alpha_ must be between 0 and 1!")
            alpha_ = [None] + list(alpha_)
        elif isinstance(alpha_, (numbers.Number)):
            if alpha_ > 1 or alpha_ < 0:
                raise ValueError("alpha_ must be between 0 and 1!")
            alpha_ = [None] + [alpha_] * (self.T-1)
        else:
            raise TypeError("alpha_ should be float/array-like instead of \
            {}!".format(type(alpha_)))
        self._set_up_CTG()
        self._delete_link_constrs()
        self.measure = "risk averse"
        for t in range(self.T):
            M = (
                self.models[t]
                if type(self.models[t]) == list
                else [self.models[t]]
            )
            for m in M:
                p_now, p_past = m.addStateVar(
                    lb=-gurobipy.GRB.INFINITY,
                    ub=gurobipy.GRB.INFINITY,
                    name="additional_state",
                )
                v = m.addVar(name="additional_var")
                m.addConstr(self.sense * (p_now-self.bound) >= 0)
                z = m.getObjective()
                # additional is \lambda_{t+1}p_t
                if t != self.T-1:
                    additional = lambda_[t+1] * p_now
                else:
                    additional = 0
                stage_cost = m.addVar(
                    name="stage_cost",
                    lb=-gurobipy.GRB.INFINITY,
                    ub=gurobipy.GRB.INFINITY,
                )
                alpha = m.alpha if t != self.T-1 else 0.0
                if t > 0:
                    if m.uncertainty_obj != {}:
                        m.addConstr(
                            z - self.discount*alpha == stage_cost,
                            uncertainty=m.uncertainty_obj,
                        )
                        m.uncertainty_obj = {}
                        m.setObjective(
                            (1 - lambda_[t])
                            * (
                                stage_cost
                                + self.discount * alpha
                                + self.discount * additional
                            )
                            + self.sense * lambda_[t] / alpha_[t] * v
                        )
                        m.addConstr(
                            v
                            >= (
                                stage_cost
                                + self.discount * alpha
                                + self.discount * additional
                                - p_past
                            )
                            * self.sense
                        )
                    else:
                        m.addConstr(z - self.discount*alpha == stage_cost)
                        m.setObjective(
                            (1-lambda_[t]) * (z + self.discount*additional)
                            + self.sense * lambda_[t] / alpha_[t] * v
                        )
                        m.addConstr(
                            v
                            >= (z + self.discount*additional - p_past)
                            * self.sense
                        )
                else:
                    m.addConstr(z - self.discount*alpha == stage_cost)
                    m.setObjective(z + self.discount*additional)
                m.update()

    def _update(self):
        self._check_first_stage_model()
        self._check_inidividual_Markovian_index()
        self._check_individual_stage_models()
        self._set_up_CTG()
        self._set_up_link_constrs()
        self._check_multistage_model()
        self._flag_update = 1

    def _get_forward_solution(self, m, t):
        solution = [None for _ in m.states]
        # avoid numerical issues
        for idx,var in enumerate(m.states):
            if var.vtype in ['B','I']:
                solution[idx] = int(round(var.X))
            else:
                if var.X < var.lb:
                    solution[idx] = var.lb
                elif var.X > var.ub:
                    solution[idx] = var.ub
                else:
                    solution[idx] = var.X
        return solution

    def _set_up_probability(self):
        """Return uniform measure if no given probability measure"""
        if self.n_Markov_states == 1:
            probability = [None for _ in range(self.T)]
            for t in range(self.T):
                m = self.models[t]
                if m.probability is not None:
                    probability[t] = m.probability
                else:
                    probability[t] = [
                        1.0/m.n_samples for _ in range(m.n_samples)
                    ]
        else:
            probability = [
                [None for _ in range(self.n_Markov_states[t])]
                for t in range(self.T)
            ]
            for t in range(self.T):
                for k in range(self.n_Markov_states[t]):
                    m = self.models[t][k]
                    if m.probability is not None:
                        probability[t][k] = m.probability
                    else:
                        probability[t][k] = [
                            1.0/m.n_samples for _ in range(m.n_samples)
                        ]
        return probability


    def _enumerate_sample_paths(self, T):
        """Enumerate all sample paths (three cases: pure stage-wise independent
        , pure Markovian, and mixed type)"""
        if self.n_Markov_states == 1:
            n_sample_paths = numpy.prod(
                [self.models[t].n_samples for t in range(T + 1)]
            )
            sample_paths = list(
                product(
                    *[range(self.models[t].n_samples) for t in range(T + 1)]
                )
            )
        else:
            n_sample_paths = numpy.prod(
                [self.models[t][0].n_samples for t in range(T + 1)]
            )
            sample_paths = list(
                product(
                    *[range(self.models[t][0].n_samples) for t in range(T + 1)]
                )
            )
            n_Markov_state_paths = numpy.prod([self.n_Markov_states])
            Markov_state_paths = list(
                product(
                    *[range(self.n_Markov_states[t]) for t in range(T + 1)]
                )
            )
            n_sample_paths = n_Markov_state_paths * n_sample_paths
            sample_paths = list(product(sample_paths, Markov_state_paths))
        return n_sample_paths, sample_paths

    def _compute_weight_sample_path(self, sample_path):
        """Compute weight/probability of (going through) a certain sample path."""
        probability = self._set_up_probability()
        T = (
            len(sample_path)
            if self.n_Markov_states == 1
            else len(sample_path[0])
        )
        if self.n_Markov_states == 1:
            weight = numpy.prod(
                [probability[t][sample_path[t]] for t in range(T)]
            )
        else:
            weight = numpy.prod(
                [
                    self.transition_matrix[t][sample_path[1][t - 1]][
                        sample_path[1][t]
                    ]
                    for t in range(1, T)
                ]
            )
            weight *= numpy.prod(
                [
                    probability[t][sample_path[1][t]][sample_path[0][t]]
                    for t in range(T)
                ]
            )
        return weight

    def _compute_current_weight_sample_path(self, sample_path):
        """Compute weight/probability of a certain node given the parent node."""
        probability = self._set_up_probability()
        t = (
            len(sample_path) - 1
            if self.n_Markov_states == 1
            else len(sample_path[0]) - 1
        )
        if self.n_Markov_states == 1:
            weight = probability[t][sample_path[t]]
        else:
            weight = (
                self.transition_matrix[t][sample_path[1][t - 1]][
                    sample_path[1][t]
                ]
                if t > 0
                else 1
            )
            weight *= probability[t][sample_path[1][t]][sample_path[0][t]]
        return weight

    # def _clean(self):
    #    for t in range(self.T - 1):
    #        M = [self.models[t]] if self.n_Markov_states == 1 else self.models[t]
    #        for m in M:
    #            if len(m.cuts) > self.Params.cleanupnumber:
    #                delete_cut = m.cuts[:-self.Params.cleanupnumber]
    #                remain_cut = m.cuts[-self.Params.cleanupnumber:]
    #                for cut in delete_cut:
    #                    m.remove(cut)
    #                m.cuts = remain_cut
    #                m.update()

    # def _cleanUP(self):
    #    ## cleanup function remove any redundant cutting planes. ##
    #    ## Caution!!! what redundant means!!! ##
    #    ## must create a temp model containing ONLY cutting planes!!! ##
    #    for t in range(self.T - 1):
    #        M = [self.models[t]] if self.n_Markov_states == 1 else self.models[t]
    #        for m in M:
    #            m.update()
    #            tempModel = m.copy()
    #
    #            constr = tempModel.getConstrs()
    #
    #            remove_index = []
    #
    #            for index, cut in enumerate(constr):
    #                if cut.sense == '>': cut.sense = '<'
    #                elif cut.sense == '<': cut.sense = '>'
    #                flag = 1
    #                for k in range(tempModel.n_samples):
    #                    tempModel._update_uncertainty(k)
    #                    tempModel.optimize()
    #                    if tempModel.status != 3:
    #                        flag = 0
    #                if flag == 1:
    #                    tempModel.remove(cut)
    #                    remove_index.append(index)
    #                else:
    #                    if cut.sense == '>': cut.sense = '<'
    #                    elif cut.sense == '<': cut.sense = '>'
    #
    #            remain_cut_name = [constr.constrname for constr in tempModel.getConstrs()]
    #            delete_cut = [constr for constr in m.cuts if constr.constrname not in remain_cut_name]
    #            remain_cut = [constr for constr in m.cuts if constr.constrname in remain_cut_name]
    #            m.remove(delete_cut)
    #            m.cuts = remain_cut
    #            ### think about why this matters!!!!!!! #####
    #            m.update()

class MSIP(MSLP):

    def _set_up_model(self):
        self.models = [StochasticModelLG(name=str(t)) for t in range(self.T)]

    def _check_individual_stage_models(self):
        """Check state variables are set properly. Check stage-wise continuous
        uncertainties are discretized."""
        if not hasattr(self, "bin_stage"):
            self.bin_stage = 0
        M = self.models[0]
        N = (
            self.models[self.bin_stage-1]
            if self.bin_stage not in [0, self.T]
            else self.models[0]
        )
        if M.states == []:
            raise Exception("State variables must be set!")
        if N.states == []:
            raise Exception("State variables must be set!")
        n_states_binary_space = M.n_states
        n_states_original_space = N.n_states
        for t in range(self.T):
            m = self.models[t]
            if m._type == "continuous":
                self._individual_type = "continuous"
                if m._flag_discrete == 0:
                    raise Exception(
                        "stage-wise independent continuous uncertainties "
                        + "must be discretized!"
                    )
            if t < self.bin_stage-1:
                if m.n_states != n_states_binary_space:
                    raise Exception(
                        "state spaces must be of the same dim for all stages!"
                    )
            else:
                if m.n_states != n_states_original_space:
                    raise Exception(
                        "state spaces must be of the same dim for all stages!"
                    )
        if self._type == "Markovian" and self._flag_discrete == 0:
            raise Exception(
                "stage-wise dependent continuous uncertainties "
                + "must be discretized!"
            )
        self.n_states = [self.models[t].n_states for t in range(self.T)]

    def _check_MIP(self):
        self.isMIP = [0] * self.T
        for t in range(self.T):
            if self.models[t].isMIP == 1:
                self.isMIP[t] = 1

    def binarize(self, precision=0, bin_stage=0):
        """Binarize MSLP.

        Parameters
        ----------
        precision: int, optional (default=0)
            The number of decimal places of accuracy

        bin_stage: int, optional (default=0)
            All stage models before bin_stage (exclusive) will be binarized.
        """
        # bin_stage should be within [0, self.T]
        self.bin_stage = int(bin_stage)
        self.bin_stage = min(self.bin_stage, self.T)
        self.bin_stage = max(0, self.bin_stage)
        precision = int(precision)
        self.precision = 10 ** precision
        # Binarize the model if bin_stage is not 0
        if self.bin_stage != 0:
            self.n_binaries = []
        # Check MSIP is qualified for binariation
        for t in range(self.bin_stage):
            n_binaries = []
            m = (
                self.models[t][0]
                if type(self.models[t]) == list
                else self.models[t]
            )
            for x in m.states:
                if (
                    x.lb == -gurobipy.GRB.INFINITY
                    or x.ub == gurobipy.GRB.INFINITY
                ):
                    raise Exception("missing bounds for the state variables!")
                elif x.lb == x.ub:
                    n_binaries.append(1)
                elif x.vtype in ["B", "I"]:
                    n_binaries.append(int(math.log2(x.ub - x.lb)) + 1)
                else:
                    n_binaries.append(
                        int(math.log2(self.precision * (x.ub - x.lb))) + 1
                    )
            if self.n_binaries == []:
                self.n_binaries = n_binaries
            else:
                if self.n_binaries != n_binaries:
                    raise Exception(
                        "bounds should be the same over time for state variables!"
                    )
        # Binarize MSIP
        for t in range(self.bin_stage):
            M = (
                [self.models[t]]
                if self.n_Markov_states == 1
                else self.models[t]
            )
            transition = (
                1
                if t == self.bin_stage-1
                and self.bin_stage not in [0, self.T]
                else 0
            )
            for m in M:
                m._binarize(self.precision, self.n_binaries, transition)

    def _update(self):
        self._check_MIP()
        super()._update()

    def _back_binarize(self):
        if not hasattr(self, "n_binaries"):
            return
        for t in range(self.bin_stage):
            M = (
                [self.models[t]]
                if self.n_Markov_states == 1
                else self.models[t]
            )
            transition = (
                1
                if t == self.bin_stage-1
                and self.bin_stage not in [0, self.T]
                else 0
            )
            for m in M:
                m._back_binarize(self.precision, self.n_binaries, transition)
        self._set_up_link_constrs()
        self.bin_stage = 0