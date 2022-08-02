# -*- coding: utf-8 -*-
"""
Created on 18 June 2020

@author: stuart.m.harwood@exxonmobil.com

An example inspired by MIRPLib Group 1 instance
https://mirplib.scl.gatech.edu/sites/default/files/LR1_2_DR1_3_VC2_V6a.txt

Some modifications
"""
import numpy as np
from itertools import product
import sequence_based.RoutingProblem as rp


def time_window(prevVisits, initial_inv, rate, tankage, size):
    """ 
    Determine the time window of this node. A single physical port must be visited multiple times
    as it runs out of inventory or fills up its storage capacity. The time window in which it must
    be visited depends on the number of times it has previously been visited

    args:
    initial_inv (float): Initial inventory level
    rate (float): Rate of change of inventory.
        rate > 0: This is a supply port
        rate < 0: This is a demand port
    tankage (float): Amount of inventory capacity at this port
    size (float): Size of a shipment/full vessel capacity

    return:
    tw_start (float): Start of time window
    tw_end (float): End of time window
    """
    # inventory(t) = initial_inv + t*rate
    if rate > 0:
        # SUPPLY
        # Earliest a ship can load a full shipload:
        # inventory(t) - (prevVisits+1)*size >= 0     
        tw0 = ((prevVisits+1)*size - initial_inv)/rate
        # latest a ship can arrive before port capacity is exceeded:
        # inventory(t) - (prevVisits)*size > tankage                
        tw1 = (tankage + (prevVisits)*size - initial_inv)/rate
        return (tw0, tw1)
    else:
        # DEMAND
        # Earliest a ship can discharge a full load into inventory:
        # inventory(t) + (prevVisits+1)*size <= tankage
        tw0 = (tankage - (prevVisits+1)*size - initial_inv)/rate
        # latest a ship can arrive before port runs out of inventory:
        # inventory(t) + (prevVisits)*size < 0                
        tw1 = (-(prevVisits)*size - initial_inv)/rate
        return (tw0, tw1)

def add_nodes(problem, name, initial_inv, rate, tankage, size, time_horizon):
    """ 
    Add nodes for this supply or demand port. A single physical port must be visited multiple times
    as it runs out of inventory or fills up its storage capacity

    args:
    problem (RoutingProblem): A routing problem to which to add nodes
    name (string): Base name of this port
    initial_inv (float): Initial inventory level
    rate (float): Rate of change of inventory.
        rate > 0: This is a supply port
        rate < 0: This is a demand port
    tankage (float): Amount of inventory capacity at this port
    size (float): Size of a shipment
    time_horizon (float): TIme horizon of problem, only nodes with time windows fully in time
        horizon are added

    Return:
    node_names (list of string): The names of the nodes that were added
    """
    if rate > 0:
        demand_level = -size
    else:
        demand_level = size
    prevVisits = 0
    node_names = []
    while True:
        TW = time_window(prevVisits, initial_inv, rate, tankage, size)
        if TW[1] > time_horizon:
            break
        # otherwise the time window is within the time horizon
        node_names.append('{}-{}'.format(name, prevVisits))
        problem.addNode(node_names[-1], TW)
        prevVisits+=1
    return node_names

def DefineProblem(TimeHorizon):
    """
    Define a specific problem given a time horizon
    """
    # Create a routing problem
    prob = rp.RoutingProblem(relaxed=True)
    
    # A depot node is required
    # CargoSize is vessel capacity
    # Set number of vessels/vehicles
    # Set Maximum number of visits in a sequence:
    # shortest travel arc is ~8, so max supply-demand trips is int(TimeHorizon/8) + 1
    # then add some slack
    CargoSize = 300
    prob.addDepot('Depot', (0,np.inf))
    prob.setMaxVehicles(7)
    prob.setMaxSequenceLength(TimeHorizon//8 + 2)

    # Define demand node data
    # Inventory is Initial inventory at start of time horizon
    # (see add_nodes)
    names =         ['D1', 'D2', 'D3']
    inventories =   [221,  215,  175]
    rates =         [-34,  -31,  -25]
    tankages =      [374,  403,  300]
    port_fee_d =    [60,   82,   94]
    
    # Add demand nodes to problem
    DemandPorts = []
    for (name, inv, rate, tank) in zip(names, inventories, rates, tankages):
        DemandPorts.append(add_nodes(prob, name, inv, rate, tank, CargoSize, TimeHorizon))

    # Define supply node data
    names =         ['S1', 'S2']
    inventories =   [220,  270]
    rates =         [47,   42]
    tankages =      [376,  420]
    port_fee_s =    [30,   85]
    
    # Add supply nodes to problem
    SupplyPorts = []
    for (name, inv, rate, tank) in zip(names, inventories, rates, tankages):
        SupplyPorts.append(add_nodes(prob, name, inv, rate, tank, CargoSize, TimeHorizon))

    # Arcs
    # Travel allowed between any supply port and any demand port (and vice versa)
    # Time and cost based on distance, costs include port fees
    # (because the nodes have a time component, not all arcs are physically reasonable-
    # but RoutingProblem checks for that)
    vessel_speed = 665.0 # km/day
    # Distances (km) between (S1, S2, D1, D2, D3) x (S1, S2, D1, D2, D3)
    distance_matrix = [[0.00,       212.34,     5305.34,    5484.21,    5459.31],
                       [212.34,     0.00,       5496.06,    5674.36,    5655.55], 
                       [5305.34,    5496.06,    0.00,       181.69,     380.30],
                       [5484.21,    5674.36,    181.69,     0.00,       386.66], 
                       [5459.31,    5655.55,    380.30,     386.66,     0.00]]
    cost_per_distance = 0.09 # dollars per km
    num_s = len(port_fee_s)
    for i, sp in enumerate(SupplyPorts):
        for j, dp in enumerate(DemandPorts):
            for s, d in product(sp, dp):
                distance = distance_matrix[i][num_s+j]
                prob.addArc(s, d, distance/vessel_speed, distance*cost_per_distance + port_fee_d[j])
                prob.addArc(d, s, distance/vessel_speed, distance*cost_per_distance + port_fee_s[i])

    # Entry arcs 
    # For simplicity allow entry to any Supply or Demand node with time window less than 14
    # No time nor cost
    for port in SupplyPorts + DemandPorts:
        for node in port:
            if prob.getNode(node).getWindow()[1] < 14:
                prob.addArc('Depot', node, 0, 0)
    
    # Exiting arcs (back to Depot)
    # For simplicity, allow exit from any "regular" supply/demand node
    # No time nor cost
    for port in SupplyPorts + DemandPorts:
        for node in port:
            prob.addArc(node, 'Depot', 0, 0)

    return prob

def getQUBO(TimeHorizon, feasibility=False):
    """
    Define the problem and actually get back the QUBO matrix
    """
    prob = DefineProblem(TimeHorizon)
    # get matrix and constant defining QUBO
    # use automatically calculated penalty parameter
    return prob.getQUBO(None,feasibility)

def getCplexProb(TimeHorizon):
    """
    Define the problem and get CPLEX object encoding problem
    """
    prob = DefineProblem(TimeHorizon)
    return prob.getCplexProb()

def test():
    prob = DefineProblem(31)
    cp = prob.getCplexProb()
    for n in prob.Nodes: print(n)
    for a in prob.Arcs.values(): print(a)
    print('Num variables: {}'.format(prob.getNumVariables()))
    prob.export_mip('ExMIRPg1.lp')
    cp.solve()
    soln = cp.solution.get_values()
    routes = prob.getRoutes(soln)
    print("\nSolution status: "+cp.solution.get_status_string())
    print("Routes (Node sequences):")
    for r in routes: 
        for n in r:
            print("{} - ".format(prob.NodeNames[n]), end='')
        print()
    return


if __name__ == "__main__":
    test()
