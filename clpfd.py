from abc import ABCMeta
import traceback
import numpy as np

# Can be a module if loaded later
pulp = None


class Solution(object):
    pass



class SolutionPulp(Solution):
    def __init__(self, status, variables):
        super(SolutionPulp, self).__init__()
        self._status = status
        self._variables = variables

    @property
    def status(self):
        return pulp.LpStatus[self._status]

    def variables_dict(self):
        """Return a dict associating the names with the values."""
        return {v.name: v.varValue for v in self._variables}

    def __str__(self):
        return str(self.variables_dict())



def solver(backend='pulp'):
    backend = backend.lower()
    if backend == 'pulp':
        return SolverPulp()
    else:
        raise ValueError("Unknown solver " + backend)



class Solver(metaclass=ABCMeta):
    def __init__(self):
        self._conststore = []
        self._variables = {}

    def add_constraint(self, c):
        """Add a constraint to be solved."""

        if c is None or isinstance(c, np.integer):
            return

        if isinstance(c, Variables):
            for v in c.flat:
                self.add_constraint(v)
            return

        for v in c.variables():
            if v.name not in self._variables:
                self._variables[v.name] = v
            elif self._variables[v.name] is not v:
                raise ValueError("Two variables with the same name in the same model")

        self._conststore.append(c)

    def solve(self, variables=None):
        raise NotImplementedError("Not implemented yet")

    def solutions(self):
        """Return an iterable generating all the solutions."""
        raise NotImplementedError("Not implemented yet")

    def all_solutions(self):
        return list(self.solutions)



class SolverPulp(Solver):
    def __init__(self):
        super(SolverPulp, self).__init__()
        self._lpvars = {}

    def solve(self, variables=None):
        global pulp
        import pulp

        prob = pulp.LpProblem()
        if variables is None:
            variables = self._variables.values()

        self._lpvars = {}
        for v in variables:
            self._add_lpvar(v)

        for c in self._conststore:
            prob += self._convert_constraint(c)

        status = prob.solve()
        variables = prob.variables()
        return SolutionPulp(status, variables)

    def _add_lpvar(self, v):
        if v.name not in self._lpvars:
            lv = pulp.LpVariable(v.name, v.domain.min, v.domain.max - 1, pulp.LpInteger)
            self._lpvars[v.name] = lv
        return self._lpvars[v.name]

    def _convert_constraint(self, c):
        if isinstance(c, (np.integer, int)):
            return c
        if isinstance(c, Variable):
            return self._add_lpvar(c)

        lpexpr = [self._convert_constraint(v) for v in c.values]

        # TODO handle specially when comparing the result of two constraints
        if c.op == '+':
            return lpexpr[0] + lpexpr[1]
        if c.op == '=':
            return lpexpr[0] == lpexpr[1]



class Domain(object):
    pass



class DomainRange(Domain):
    def __init__(self, low=None, up=None):
        self.min = low
        self.max = up

    @staticmethod
    def fromrange(r):
        assert isinstance(r, range), "DomainRange.fromrange only accepts range objects"
        assert r.step == 1, "Sparse ranges not implemented yet"
        return DomainRange(r.start, r.stop)



class Expression(object):
    def __init__(self, op, *values):
        self.op = op
        self.values = values

        for v in values:
            assert isinstance(v, (Expression, int, np.integer)), \
                "Can only build expressions out of expressions or integers. Got: %s" % type(v)

    def __add__(self, value):
        return Expression('+', self, value)

    def __radd__(self, value):
        return Expression('+', value, self)

    def __eq__(self, value):
        return Expression('=', self, value)

    def __str__(self):
        return str(self.values[0]) + " " + self.op + " " + str(self.values[1])

    def variables(self):
        if isinstance(self, Variable):
            return [self]
        return sum([v.variables() for v in self.values if isinstance(v, Expression)], [])



class Variable(Expression):
    _varidx = 0

    @classmethod
    def new_name(cls):
        name = "var_%d" % Variable._varidx
        Variable._varidx += 1
        return name

    def __init__(self, domain=None, name=None):
        super(Variable, self).__init__(None)

        if name is None:
            name = self.new_name()

        if domain is None:
            domain = DomainRange()
        elif isinstance(domain, range):
            domain = DomainRange.fromrange(domain)

        self.name = name
        self.domain = domain

    def __str__(self):
        return self.name

    def __hash__(self):
        return hash(self.name)



class Variables(np.ndarray):
    """
    A convenient class to manipulate arrays of Variable.
    """

    # To unstandand this black magic, refer to the numpy documentation about
    # subclassing ndarray.
    def __new__(subtype, shape, domain=None, name_prefix=None):
        arr = super(Variables, subtype).__new__(subtype, shape, dtype=np.object)
        if name_prefix is None:
            name_prefix = Variable.new_name()

        arr.flat = [Variable(domain, "%s_%d" % (name_prefix, i)) for i in range(len(arr.flat))]

        return arr

    def __array_finalize__(self, obj):
        pass

    def _call_ufunc(self, ufunc, method, *inputs, **kwargs):
        if ufunc == np.equal and method == '__call__':
            bc = np.broadcast(*inputs)
            results = kwargs.get('out', None)
            if results is None:
                results = np.empty(bc.shape, dtype=np.object)
            results.flat = [(a == b) for a, b in bc]
        else:
            results = super(Variables, self).__array_ufunc__(ufunc, method, *inputs, **kwargs)
            if results is NotImplemented:
                return NotImplemented

        return results

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        # This is needed because numpy doesn't want the comparison ufuncs to
        # return anything else than booleans.
        # https://github.com/numpy/numpy/issues/10948
        try:
            inputs = [i.view(np.ndarray) if isinstance(i, Variables) else i for i in inputs]

            outputs = kwargs.pop('out', None)
            if outputs is not None:
                # The output to convert back to Variables object
                convout = [isinstance(o, Variables) for o in outputs]
                # kwargs['out'] must be a tuple
                kwargs['out'] = tuple(o.view(np.ndarray) if v else o for v, o in zip(convout, outputs))
            else:
                convout = [True] * ufunc.nout

            results = self._call_ufunc(ufunc, method, *inputs, **kwargs)

            if ufunc.nout == 1:
                results = (results,)

            results = tuple(np.asarray(r).view(Variables) if v else r for v, r in zip(convout, results))

            return results[0] if ufunc.nout == 1 else results

        except BaseException as e:
            print("raised in __array_ufunc__:")
            traceback.print_exc()



def main():
    s = solver()
    a = Variable()
    b = Variable()
    c = Variable()
    s.add_constraint(a + b == c)
    print(s.solve())

    v = Variables((2, 2))
    #print(np.sum(v, axis=0))
    v += 0
    print(v == 0)



if __name__ == '__main__':
    main()
