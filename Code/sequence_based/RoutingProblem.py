"""
Created on 6 December 2019

@author: stuart.m.harwood@exxonmobil.com

Container for a sequence-based formulation of a
Vehicle Routing Problem with Time Windows
"""
import time
import numpy
import scipy.sparse as sparse
from itertools import product
try:
    import cplex
except ImportError:
    pass

class RoutingProblem:
    """
    Vehicle Routing Problem with Time Windows (VRPTW)
    as a sequence-based formulation
    becomes a Binary Polynomial Equality-Constrained (BPEC)
    optimization problem
    which can be transformed to a
    Quadratic Unconstrained Binary Optimization (QUBO) problem
    """
    # pylint: disable=too-many-instance-attributes

    def __init__(self, relaxed=False):
        # basic data
        # Allow slightly less physical arcs, to potentially get feasible formulation
        self.relaxed = relaxed
        # maximum length of a sequence/number of positions to consider
        self.maxSequenceLength=0
        # maximum number of vehicles to consider
        self.maxVehicles=0
        self.Nodes = []
        self.NodeNames = []
        self.Arcs = dict()
        self.vehicle_cost = []
        self.NumVariables = 0
        self.VarMapping = []
        self.fixedValues = dict()
        self.variablesEnumerated = False
        self.objective_built = False
        self.lin_con_built = False
        self.quad_con_built = False

        # Parameters of the base formulation:
        # Binary Polynomial Equality-Constrained (BPEC) problem
        self.bpec_c = None
        self.bpec_q = None
        self.bpec_edgepenalty_bilinear = None
        self.bpec_constraints_matrix = None
        self.bpec_constraints_rhs = None

    def setMaxSequenceLength(self, maxSequenceLength):
        self.maxSequenceLength = int(maxSequenceLength)
        return

    def setMaxVehicles(self, max_vehicles):
        self.maxVehicles = max_vehicles
        self.vehicle_cost = [0]*max_vehicles
        return

    def addNode(self,NodeName,TW):
        assert NodeName not in self.NodeNames, NodeName + ' is already in Node List'
        self.Nodes.append(Node(NodeName,TW))
        self.NodeNames.append(NodeName)
        return

    def addDepot(self,DepotName,TW):
        """Insert depot at first position of nodes"""
        if not numpy.isinf(TW[1]):
            print("Consider making Depot time window infinite in size...")
        try:
            if self.NodeNames[0] != DepotName:
                self.Nodes.insert(0,Node(DepotName,TW))
                self.NodeNames.insert(0,DepotName)
            else:
                print("Depot already added")
        except IndexError:
            self.addNode(DepotName, TW)
        # Since we treat the depot as absorbing
        # (see build_bpec_quadratic_constraints)
        # We should allow Depot - Depot moves
        self.Arcs[(0,0)] = Arc(self.Nodes[0],self.Nodes[0],0,0)
        return

    def getNodeIndex(self, NodeName):
        return self.NodeNames.index(NodeName)

    def getNode(self, NodeName):
        return self.Nodes[self.getNodeIndex(NodeName)]

    def addArc(self,OName,DName,time,cost=0):
        """
        Add a potentially allowable arc;
        we also check feasibility of TIMING:
        If Origin = Depot OR Relaxed formulation:
            allow if origin TimeWindow START plus travel time < destination TimeWindow end
        Else (Origin != Depot):
            allow if origin TimeWindow END   plus travel time < destination TimeWindow end

        Note that this is a conservative way to enforce timing (unless relaxed);
        this formulation cannot enforce it much more finely
        """
        i = self.getNodeIndex(OName)
        j = self.getNodeIndex(DName)
        if i == 0 or self.relaxed:
            # origin is depot, OR this is the relaxed formulation
            departure_time = self.Nodes[i].getWindow()[0]
        else:
            departure_time = self.Nodes[i].getWindow()[1]
        # Add arc if timing works:
        if departure_time + time <= self.Nodes[j].getWindow()[1]:
            self.Arcs[(i,j)] = Arc(self.Nodes[i],self.Nodes[j],time,cost)
        return

    def checkArc(self, arc_key):
        """ Is the arc valid? """
        return arc_key in self.Arcs
        # try :
        #     self.Arcs[arcKey]
        #     return True
        # except KeyError:
        #     return False

    def enumerateVariables(self):
        """ Basic operation that needs to be done to keep track of
            variable counts, indexing """
        if self.variablesEnumerated:
            return

        # "inverse" var map - from tuple index to enumerated index
        # use -1 to indicate the var is fixed
        self.VarMapping_inverse = -numpy.ones(
            (self.maxVehicles, self.maxSequenceLength, len(self.Nodes)),
            dtype=int
        )
        start = time.time()
        num_vars = 0
        # Loop over (vehicles, positions/sequence, nodes)
        # and check if a variable is free/not fixed
        for si in range(self.maxSequenceLength):
            for ni in range(len(self.Nodes)):
                # Is the variable (vi,si,ni) fixed?
                # Check the rules:
                # We must start in the depot:
                if si == 0 and ni == 0:
                    for vi in range(self.maxVehicles):
                        self.fixedValues[(vi,si,ni)] = 1.0
                    continue
                # We must NOT start anywhere besides depot:
                if si == 0 and ni != 0:
                    for vi in range(self.maxVehicles):
                        self.fixedValues[(vi,si,ni)] = 0.0
                    continue
                # We must follow allowed edges from depot
                if si == 1 and not self.checkArc((0,ni)):
                    for vi in range(self.maxVehicles):
                        self.fixedValues[(vi,si,ni)] = 0.0
                    continue
                # We must END in the depot:
                if si == self.maxSequenceLength-1 and ni == 0:
                    for vi in range(self.maxVehicles):
                        self.fixedValues[(vi,si,ni)] = 1.0
                    continue
                # We must NOT end anywhere besides depot:
                if si == self.maxSequenceLength-1 and ni != 0:
                    for vi in range(self.maxVehicles):
                        self.fixedValues[(vi,si,ni)] = 0.0
                    continue
                # We must follow allowed edges back to depot
                if si == self.maxSequenceLength-2 and not self.checkArc((ni,0)):
                    for vi in range(self.maxVehicles):
                        self.fixedValues[(vi,si,ni)] = 0.0
                    continue
                # At this point, variable (vi,si,ni) is free, record the index
                for vi in range(self.maxVehicles):
                    self.VarMapping.append((vi,si,ni))
                    self.VarMapping_inverse[vi,si,ni] = num_vars
                    num_vars += 1
        # end loops
        duration = time.time() - start
        print("Variable enumeration took {} seconds".format(duration))
        self.NumVariables = num_vars
        self.variablesEnumerated = True
        #print("Fixed: {}".format(self.fixedValues))
        return

    def getNumVariables(self):
        """ Number of variables in formulation """
        return self.NumVariables

    def getVarIndex(self, vehicle_index, sequence_index, node_index):
        """Get the unique id/index of the binary variable given the "tuple" indexing
           Return of None means the tuple corresponds to a fixed variable """
        index = self.VarMapping_inverse[vehicle_index, sequence_index, node_index]
        if index < 0:
            return None
        # else
        return index
        # try:
        #     return self.VarMapping.index((vehicle_index, sequence_index, node_index))
        # except ValueError:
        #     return None

    def getVarTupleIndex(self, var_index):
        """Inverse of getVarIndex"""
        try:
            return self.VarMapping[var_index]
        except IndexError:
            return None

    def build_bpec_obj(self):
        """
        Build up objective of base BPEC formulation:

        bpec_q: quadratic coefficients
        bpec_c: linear coefficients
        """
        if self.objective_built: return
        self.enumerateVariables()

        # linear terms of objective
        self.bpec_c = numpy.zeros(self.getNumVariables())
        # quadratic (bilinear) terms of objective
        qval = []
        qrow = []
        qcol = []

        # Linear and Bilinear terms
        for vi in range(self.maxVehicles):
            for si in range(self.maxSequenceLength-1):
                for (ni,nj) in self.Arcs.keys():
                    key = (ni,nj)
                    var_index_1 = self.getVarIndex(vi,si,ni)
                    var_index_2 = self.getVarIndex(vi,si+1,nj)

                    # if both variables fixed, this goes to constant part of obj
                    # if exactly one is fixed, this goes to linear part
                    # if no variable is fixed, this goes to quadratic part
                    constant_bool = ((var_index_1 is None) and (var_index_2 is None))
                    linear_bool   = ((var_index_1 is None) !=  (var_index_2 is None)) # exclusive or
                    quad_bool = not ((var_index_1 is None) or  (var_index_2 is None))

                    coeff = self.Arcs[key].getCost() + self.vehicle_cost[vi]
                    if constant_bool:
                        constant = self.fixedValues[(vi,si,ni)]*self.fixedValues[(vi,si+1,nj)]
                        if constant != 0:
                            print("Kind of weird... not gonna track constant part of objective")
                    if linear_bool:
                        if var_index_1 is None:
                            coeff *= self.fixedValues[(vi,si,ni)]
                            var_index = var_index_2
                        if var_index_2 is None:
                            coeff *= self.fixedValues[(vi,si+1,nj)]
                            var_index = var_index_1
                        self.bpec_c[var_index] += coeff
                    if quad_bool:
                        # Add it in "symmetrically" ?
                        qrow.append(var_index_1)
                        qcol.append(var_index_2)
                        qval.append(coeff)
        # construct sparse matrix for bilinear terms
        M = self.getNumVariables()
        self.bpec_q = sparse.coo_matrix((qval,(qrow,qcol)), shape=(M,M))
        self.objective_built = True
        return

    def build_bpec_quadratic_constraints(self):
        """
        Build up quadratic constraints of base BPEC formulation
        """
        if self.quad_con_built: return
        self.enumerateVariables()

        # Certain arcs cannot be used.
        # These arcs must be penalized in the objective.
        # Keep track with bpec_edgepenalty_bilinear

        start = time.time()
        # for bilinear constraint/penalty terms
        pqrow = []
        pqcol = []

        # Constraint: Only use allowed arcs
        # x_{vi,si,ni} * x_{vi,si+1,nj} = 0, \forall vi,si,(ni,nj) \notin Arcs
        for ni, nj in product(range(len(self.Nodes)),range(len(self.Nodes))):
            # If this is a valid arc, then there is no constraint
            if self.checkArc((ni,nj)):
                continue
            for si in range(self.maxSequenceLength-1):
                for vi in range(self.maxVehicles):
                    pqrow, pqcol = self.quadratic_constraint_logic(
                        vi, si, ni, nj, pqrow, pqcol
                    )

        # Constraint: Once a vehicle returns to depot, it remains there
        # x_{vi,si,d}  * x_{vi,si+1,nj} = 0, \forall vi, si >= 1, nj \neq d
        for nj in range(1,len(self.Nodes)):
            # If this is not a valid arc, then we already added this constraint above
            if not self.checkArc((0,nj)):
                continue
            for si in range(1,self.maxSequenceLength-1):
                for vi in range(self.maxVehicles):
                    pqrow, pqcol = self.quadratic_constraint_logic(
                        vi, si, 0, nj, pqrow, pqcol
                    )

        # construct sparse matrix for bilinear terms
        n_var = self.getNumVariables()
        pqval = numpy.ones(len(pqrow))
        self.bpec_edgepenalty_bilinear = sparse.coo_matrix((pqval,(pqrow,pqcol)),
            shape=(n_var,n_var)
        )
        self.quad_con_built = True
        duration = time.time() - start
        print("Quadratic constraints built in {} seconds".format(duration))
        return

    def quadratic_constraint_logic(self, vi, si, ni, nj, pqrow, pqcol):
        """
        Consistently check the quadratic constraints and update sparse
        representation of the matrix
        """
        var_index_1 = self.getVarIndex(vi,si,ni)
        var_index_2 = self.getVarIndex(vi,si+1,nj)

        # # if one or both variables fixed, check consistency
        # # if no variable is fixed, this goes to quadratic constraints
        # constant_bool = ((var_index_1 is None) and (var_index_2 is None))
        # linear_bool   = ((var_index_1 is None) !=  (var_index_2 is None)) # exclusive or
        # quad_bool = not ((var_index_1 is None) or  (var_index_2 is None))

        # BOTH VARS FIXED
        if (var_index_1 is None) and (var_index_2 is None):
            # constraint is x_i * x_j = 0
            fixed_val = self.fixedValues[(vi,si,ni)]* \
                        self.fixedValues[(vi,si+1,nj)]
            assert numpy.isclose(fixed_val, 0.0), \
                "Quadratic constraint not consistent"
        # ONE AND ONLY ONE VAR FIXED (exclusive or)
        elif (var_index_1 is None) != (var_index_2 is None):
            # constraint is x_i * x_j = 0
            # If only one value is fixed, it must be zero,
            # otherwise we have missed a chance to fix a variable
            if var_index_1 is None:
                fixed_val = self.fixedValues[(vi,si,ni)]
                missed_var_index = (vi,si+1,nj)
            else:
                fixed_val = self.fixedValues[(vi,si+1,nj)]
                missed_var_index = (vi,si,ni)
            assert numpy.isclose(fixed_val, 0.0), \
                f"Missed chance to fix variable {missed_var_index}"
        # NEITHER VAR FIXED
        else:
            # Either invalid arc or enforcing depot absorption
            # Update quadratic constraint/penalty
            pqrow.append(var_index_1)
            pqcol.append(var_index_2)
        return pqrow, pqcol

    def build_bpec_constraints(self):
        """
        Build up Linear equality constraints of BPEC
        A*x = b
        """
        if self.lin_con_built: return
        self.enumerateVariables()

        start = time.time()
        aval = []
        arow = []
        acol = []
        brhs = []
        self.lin_con_names = []

        row_index = 0
        # Each node (except depot) is visited exactly once
        for ni in range(1,len(self.Nodes)):
            # right-hand side value is one
            brhs.append(1.0)
            self.lin_con_names.append("c_node{}".format(ni))
            # sum over sequence and vehicle indices
            for si in range(self.maxSequenceLength):
                for vi in range(self.maxVehicles):
                    var_index = self.getVarIndex(vi,si,ni)
                    if var_index is None:
                        # Fixed variable.
                        # "move" it to right-hand side
                        brhs[-1] -= self.fixedValues[(vi,si,ni)]
                        continue
                    arow.append(row_index)
                    acol.append(var_index)
                    aval.append(1.0)
                # end for
            # end for
            row_index += 1

        # For each vehicle, each sequence index is used exactly once
        # The first and last sequence positions are automatically satisfed by fixed variable values
        for si in range(1,self.maxSequenceLength-1):
            for vi in range(self.maxVehicles):
                # right-hand side value is one
                brhs.append(1.0)
                self.lin_con_names.append("c_v{}s{}".format(vi,si))
                # sum over all nodes
                for ni in range(len(self.Nodes)):
                    var_index = self.getVarIndex(vi,si,ni)
                    if var_index is None:
                        # Fixed variable.
                        # "move" it to right-hand side
                        brhs[-1] -= self.fixedValues[(vi,si,ni)]
                        continue
                    arow.append(row_index)
                    acol.append(var_index)
                    aval.append(1.0)
                # end for
                row_index += 1

        self.bpec_constraints_matrix = sparse.coo_matrix((aval,(arow,acol)))
        self.bpec_constraints_rhs = numpy.array(brhs)
        self.lin_con_built = True
        duration = time.time() - start
        print("Linear constraints built in {} seconds".format(duration))
        return

    def make_feasible(self, high_cost):
        """
        Some sort of greedy construction heuristic to make sure the problem is
        feasible. We add dummy nodes/arcs as necessary to emulate more
        vehicles being available.
        """
        # Initialize list of unvisited node indices
        # remove depot
        # then sort based on time window end - useful later
        unvisited_indices = list(range(len(self.Nodes)))
        unvisited_indices.remove(0)
        unvisited_indices.sort(key=lambda n: self.Nodes[n].getWindow()[1])

        depot_nm = self.NodeNames[0]
        used_sequences = []
        for vi in range(self.maxVehicles):
            # We always start and end in depot; the corresponding variables are fixed
            current_node = 0
            for si in range(1,self.maxSequenceLength-1):
                # Although timing is less important in this formulation,
                # we still choose the next node based on the earliest next departure time,
                # as this will be a proxy for how much flexibility/ how many more
                # nodes we can visit on this route
                no_next_node = True
                for ni in unvisited_indices:
                    arc = (current_node, ni)
                    if self.checkArc(arc):
                        # unvisited_indices is sorted, so the first node to which
                        # we have a valid arc is the node with best timing
                        used_sequences.append((vi, si, ni))
                        unvisited_indices.remove(ni)
                        current_node = ni
                        no_next_node = False
                        break
                if no_next_node:
                    # We can't go to any unvisited nodes
                    # finish out the route at the depot
                    arc = (current_node, 0)
                    if not self.checkArc(arc):
                        node_nm = self.NodeNames[current_node]
                        self.addArc(node_nm, depot_nm, 0, 0)
                        print(f"Adding arc {node_nm} -- {depot_nm}")
                    for sii in range(si, self.maxSequenceLength-1):
                        used_sequences.append((vi, sii, 0))
                    break
            # end sequence loop
        # end vehicle loop

        for ni in unvisited_indices:
            # We are changing data - variables counts will change
            self.variablesEnumerated = False
            self.objective_built = False
            self.lin_con_built = False
            self.quad_con_built = False

            # add a vehicle and entry arc from depot
            # Arcs from the depot could be "available"
            # So add a cost for this dummy *vehicle*
            vi = self.maxVehicles
            self.maxVehicles += 1
            self.vehicle_cost.append(high_cost)
            print(f"Adding vehicle {vi} with cost {high_cost}")
            arc = (0, ni)
            node_nm = self.NodeNames[ni]
            # check and add entry arc
            if not self.checkArc((0, ni)):
                self.addArc(depot_nm, node_nm, 0, high_cost)
                print(f"Adding arc {depot_nm} -- {node_nm}")
            used_sequences.append((vi, 1, ni))
            # check and add exit arc
            if not self.checkArc((ni, 0)):
                self.addArc(node_nm, depot_nm, 0, high_cost)
                print(f"Adding arc {node_nm} -- {depot_nm}")
            # finish out sequence at depot
            for si in range(2, self.maxSequenceLength-1):
                used_sequences.append((vi, si, 0))
        # end modifying problem to make feasible

        # construct and save feasible solution
        self.enumerateVariables()
        self.feasible_solution = numpy.zeros(self.NumVariables)
        for seq in used_sequences:
            self.feasible_solution[self.getVarIndex(*seq)] = 1
        return

    def getCplexProb(self):
        """
        Get a CPLEX object containing the BLEC/MIP representation
        """
        self.build_bpec_obj()
        self.build_bpec_constraints()
        self.build_bpec_quadratic_constraints()

        # Define object
        cplex_prob = cplex.Cplex()
        cplex_prob.objective.set_sense(cplex_prob.objective.sense.minimize)
        # Variables: all binary
        # can't go too wild with names otherwise .lp format doesn't like it
        namer = lambda t: "v{}s{}n{}".format(t[0], t[1], t[2])
        names = list(map(namer, self.VarMapping))
        var_types = [cplex_prob.variables.type.binary] * self.getNumVariables()
        cplex_prob.variables.add(obj=self.bpec_c.tolist(), types=var_types, names=names)

        # Linear constraints: all equality
        lcon_types = ['E'] * len(self.bpec_constraints_rhs)
        rows = self.bpec_constraints_matrix.row.tolist()
        cols = self.bpec_constraints_matrix.col.tolist()
        vals = self.bpec_constraints_matrix.data.tolist()
        cplex_prob.linear_constraints.add(
            rhs=self.bpec_constraints_rhs.tolist(),
            senses=lcon_types, 
            names=self.lin_con_names
        )
        cplex_prob.linear_constraints.set_coefficients(zip(rows, cols, vals))

        # Bilinear constraints: LINEARIZE
        # x_i * x_j = 0 for certain i,j
        # <==>
        # x_i + x_j <= 1 (when vars are binary)
        rows = self.bpec_edgepenalty_bilinear.row
        cols = self.bpec_edgepenalty_bilinear.col
        vals = self.bpec_edgepenalty_bilinear.data
        assert (vals == 1.0).all(), "Linearization plan not gonna work"
        linearized = [cplex.SparsePair(ind = [int(r), int(c)], val = [1.0, 1.0])
            for r,c in zip(rows, cols)]
        names = ["c_linearized_{}_{}".format(r, c) for r,c in zip(rows, cols)]
        num_to_add = len(linearized)
        cplex_prob.linear_constraints.add(
            lin_expr=linearized,
            senses=['L']*num_to_add,
            rhs=[1.0]*num_to_add,
            names=names)

        # Quadratic objective
        rows = self.bpec_q.row.tolist()
        cols = self.bpec_q.col.tolist()
        vals = self.bpec_q.data.tolist()
        cplex_prob.objective.set_quadratic_coefficients(zip(rows,cols,vals))

#        # Quadratic objective PLUS quadratic constraints as penalty:
#        pen_param = 1.0 + self.get_sufficient_penalty(feasibility=False)
#        complete_q = (self.bpec_q + pen_param*self.bpec_edgepenalty_bilinear).tocoo()
#        rows = complete_q.row.tolist()
#        cols = complete_q.col.tolist()
#        vals = complete_q.data.tolist()
#        cplex_prob.objective.set_quadratic_coefficients(zip(rows,cols,vals))
        return cplex_prob
    
    def get_sufficient_penalty(self,feasibility):
        """ 
        Get the threshhold value of penalty parameter for penalizing constraints 
        Actual penalty value must be STRICTLY GREATER than this
        """
        if feasibility:
            sufficient_pp = 0.0
        else:
            sum_arc_cost = sum([numpy.fabs(arc.getCost()) for arc in self.Arcs.values()])
            sufficient_pp = self.maxSequenceLength*self.maxVehicles*sum_arc_cost
        return sufficient_pp

    def getConstraintData(self):
        """
        Return constraints in a consistent way
        A_eq * x = b_eq
        xᵀ * Q_eq * x = r_eq

        Parameters:

        Return:
            A_eq (array): 2-d array of linear equality constraints
            b_eq (array): 1-d array of right-hand side of equality constraints
            Q_eq (array): 2-d array of a single quadratic equality constraint
                (potentially all zeros if there are no nontrivial quadratic constraints)
            r_eq (float): Right-hand side of the quadratic constraint
        """
        self.build_bpec_constraints()
        self.build_bpec_quadratic_constraints()
        A_eq = self.bpec_constraints_matrix
        b_eq = self.bpec_constraints_rhs
        # # as in getCplexProb, linearize bilinear constraints
        # # x_i * x_j = 0
        # # <==>
        # # x_i + x_j <= 1 (when vars are binary)
        # row = []
        # col = []
        # val = []
        # rhs = []
        # for k in range(self.bpec_q.nnz):
        #     # for each nonzero (bilinear) term
        #     # we have an inequality constraint (a new row)
        #     # and two nonzero entries in that row
        #     row.append(k)
        #     col.append(self.bpec_q.row[k])
        #     val.append(1.0)
        #     row.append(k)
        #     col.append(self.bpec_q.col[k])
        #     val.append(1.0)
        #     rhs.append(1.0)
        # n = self.getNumVariables()
        Q_eq = sparse.csr_matrix(self.bpec_edgepenalty_bilinear)
        r_eq = 0
        # if anything is empty, make sure its dense
        if len(b_eq) == 0: A_eq = A_eq.toarray()
        return A_eq, b_eq, Q_eq, r_eq

    def getQUBO(self, penalty_parameter=None, feasibility=False):
        """
        Get the Quadratic Unconstrained Binary Optimization problem
        reformulation of the BPEC
        penalty_parameter : value of penalty parameter to use for reformulation
            Default: None (determined by arc costs)
        feasibility : define feasibility problem only
            Default: False
        """
        self.build_bpec_obj()
        self.build_bpec_constraints()
        self.build_bpec_quadratic_constraints()

        sufficient_pp = self.get_sufficient_penalty(feasibility)
        if penalty_parameter is None:
            penalty_parameter = sufficient_pp + 1.0
        if penalty_parameter <= sufficient_pp:
            print("Penalty parameter might not be big enough...(>{})".format(sufficient_pp))

        qval = []
        qrow = []
        qcol = []

        # according to scipy.sparse documentation,
        # (https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.coo_matrix.html#scipy.sparse.coo_matrix)
        # Duplicated entries are merely summed to together when converting to an array or other sparse matrix type
        # This is consistent with our aim
        
        if not feasibility:
            # Linear objective terms:
            for i in range(self.getNumVariables()):
                if self.bpec_c[i] != 0:
                    qrow.append(i)
                    qcol.append(i)
                    qval.append(self.bpec_c[i])
            # Quadratic objective terms:
            for (r,c,val) in zip(self.bpec_q.row,self.bpec_q.col,self.bpec_q.data):
                qrow.append(r)
                qcol.append(c)
                qval.append(val)

        # Quadratic edge penalty terms:
        for (r,c,val) in zip(self.bpec_edgepenalty_bilinear.row,
                             self.bpec_edgepenalty_bilinear.col,
                             self.bpec_edgepenalty_bilinear.data):
            qrow.append(r)
            qcol.append(c)
            qval.append(penalty_parameter*val)

        # Linear Equality constraints:
        # rho*||Ax - b||^2 = rho*( x^T (A^T A) x - 2b^T A x + b^T b )

        # Put -2b^T A on the diagonal:
        TwoBTA = -2*self.bpec_constraints_matrix.transpose().dot(self.bpec_constraints_rhs)
        for i in range(self.getNumVariables()):
            if TwoBTA[i] != 0:
                qrow.append(i)
                qcol.append(i)
                qval.append(penalty_parameter*TwoBTA[i])

        # Construct the QUBO objective matrix so far
        Q = sparse.coo_matrix((qval,(qrow,qcol)))

        # Add A^T A to it
        # This will be some sparse matrix (probably CSR format)
        Q = Q + penalty_parameter*self.bpec_constraints_matrix.transpose().dot(self.bpec_constraints_matrix)

        # constant term of QUBO objective
        constant = penalty_parameter*self.bpec_constraints_rhs.dot(self.bpec_constraints_rhs)
        return Q, constant

    def getRoutes(self, solution):
        """
        Get a representation of the paths/ vehicle routes in a solution
        
        solution: binary vector corresponding to a solution
        """
        soln_var_indices = numpy.flatnonzero(solution)
        if soln_var_indices.size == 0:
            print("A strange game. The only winning move is not to play")
            return []
        # The indices are (vehicle, position, node)
        # If we lexicographically sort them we automatically get the routes for each vehicle
        # Add in the fixed values (enforcing that vehicles start at depot)
        # Flip the tuples because numpy.lexsort sorts on last row, second to last row, ...
        soln_var_tuples = [self.getVarTupleIndex(k) for k in soln_var_indices]
        soln_var_tuples += [t for t,v in self.fixedValues.items() if v == 1.0]
        tuples_to_sort = numpy.flip(numpy.array(soln_var_tuples), -1)
        arg_sorted = numpy.lexsort(tuples_to_sort.T)
        tuples_ordered = [soln_var_tuples[i] for i in arg_sorted]
        # A dirty print
        print("tuples_ordered={}".format(tuples_ordered))

        routes = []
        # Build up routes and do dummy checks
        for vi in range(self.maxVehicles):
            routes.append([])
            prev_node = None
            for si in range(self.maxSequenceLength):
                t = tuples_ordered.pop(0)
                curr_node = t[2]
                if t[0] != vi or t[1] != si:
                    print("Unexpected tuple {} in solution".format(t))
                    continue
                if prev_node and not self.checkArc((prev_node, curr_node)):
                    print("Solution uses unallowed arc {}".format((prev_node, curr_node)))
                    continue
                routes[-1].append(curr_node)
                prev_node = curr_node
        return routes

    def solveCplexProb(self, filename_sol='cplex.sol'):
        cplex_prob = self.getCplexProb()
        cplex_prob.solve()
        cplex_prob.solution.write(filename_sol)
        return

    def export_mip(self, filename=None):
        """ Export representation of problem (defaults to .lp)"""
        if filename is None:
            filename = 'sequence_based_rp.lp'
        cplex_prob = self.getCplexProb()
        cplex_prob.write(filename)
        return


    def print_objective(self):
        """Just display the sparse matrix encoding linear/bilinear terms in a nice way"""
        print("Linear terms:")
        for i in range(self.getNumVariables()):
            (vi,si,ni) = self.getVarTupleIndex(i)
            print("v{}, s{}, {}: {}".format(vi,si,self.NodeNames[ni], self.bpec_c[i]))

        print("Quadratic terms:")
        for (r,c,val) in zip(self.bpec_q.row,self.bpec_q.col,self.bpec_q.data):
            (vi,si,ni) = self.getVarTupleIndex(r)            
            (vj,sj,nj) = self.getVarTupleIndex(c)
            print("(v{}, s{}, {}) -- (v{}, s{}, {}) : {}".format(
                    vi,si,self.NodeNames[ni], vj,sj,self.NodeNames[nj], val))

    def print_edge_penalty(self):
        """Just display the sparse matrix encoding the edge penalty bi/linear terms in a nice way"""
        print("Quadratic Edge Penalty terms:")
        for (r,c,val) in zip(self.bpec_edgepenalty_bilinear.row,
                             self.bpec_edgepenalty_bilinear.col,
                             self.bpec_edgepenalty_bilinear.data):
            (vi,si,ni) = self.getVarTupleIndex(r)            
            (vj,sj,nj) = self.getVarTupleIndex(c)
            print("(v{}, s{}, {}) -- (v{}, s{}, {}) : (not allowed)".format(
                    vi,si,self.NodeNames[ni], vj,sj,self.NodeNames[nj]))

    def print_qubo(self, Q=None):
        """ Print QUBO matrix Q all pretty with var names """
        if Q is None:
            Q, _ = self.getQUBO()

        Qcoo = Q.tocoo()
        for (r,c,val) in zip(Qcoo.row,Qcoo.col,Qcoo.data):
            (vi,si,ni) = self.getVarTupleIndex(r)            
            (vj,sj,nj) = self.getVarTupleIndex(c)
            print("(v{}, s{}, {}) -- (v{}, s{}, {}) : {}".format(
                    vi,si,self.NodeNames[ni], vj,sj,self.NodeNames[nj], val))


class Node:
    """
    A node is a customer, which must be visited in a particular window of time
    """
    def __init__(self, name, TW):
        assert TW[0] <= TW[1], 'Time window for {} not valid: {} > {}'.format(name, TW[0], TW[1])
        self.name = name
        self.tw = TW
    def getName(self):
        return self.name
    def getWindow(self):
        return self.tw
    def __str__(self):
        return "{}: in {}".format(self.name,self.tw)


class Arc:
    """
    An arc goes from one node to another (distinct) node
    It has an associated travel time, and a cost
    """
    def __init__(self,From,To,TravelTime,Cost):
        #assert From is not To, 'Arc endpoints must be distinct'
        self.origin = From
        self.destination = To
        self.traveltime = TravelTime
        self.cost = Cost
    def getO(self):
        return self.origin
    def getD(self):
        return self.destination
    def getTravelTime(self):
        return self.traveltime
    def getCost(self):
        return self.cost
    def __str__(self):
        return "{} to {}; t={:.2f}".format(self.origin.name, self.destination.name, self.traveltime)

