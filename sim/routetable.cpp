// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-  
#include <climits>
#include "routetable.h"
#include "network.h"
#include "queue.h"
#include "pipe.h"

void RouteTable::addRoute(int destination, Route* port, int cost, packet_direction direction){  
    if (_fib.find(destination) == _fib.end())
        _fib[destination] = new vector<FibEntry*>(); 
    
    assert(port!=NULL);

    _fib[destination]->push_back(new FibEntry(port,cost,direction));
}

void RouteTable::addHostRoute(int destination, Route* port, int flowid){  
    if (_hostfib.find(destination) == _hostfib.end())
        _hostfib[destination] = new unordered_map<int, HostFibEntry*>(); 
    
    assert(port!=NULL);

    (*_hostfib[destination])[flowid] = new HostFibEntry(port,flowid);
}


vector<FibEntry*>* RouteTable::getRoutes(int destination){
    if (_fib.find(destination) == _fib.end())
        return NULL;
    else        
        return _fib[destination];
}

void RouteTable::addLeafRoute(int destination_leaf, Route* port, int cost,
                              packet_direction direction) {
    if (_leaf_fib.find(destination_leaf) == _leaf_fib.end())
        _leaf_fib[destination_leaf] = new vector<FibEntry*>();
    assert(port != NULL);
    _leaf_fib[destination_leaf]->push_back(
        new FibEntry(port, cost, direction));
}

vector<FibEntry*>* RouteTable::getLeafRoutes(int destination_leaf) {
    if (_leaf_fib.find(destination_leaf) == _leaf_fib.end())
        return NULL;
    return _leaf_fib[destination_leaf];
}

void RouteTable::setLeafRoutes(int destination_leaf,
                               vector<FibEntry*>* routes) {
    _leaf_fib[destination_leaf] = routes;
}

HostFibEntry* RouteTable::getHostRoute(int destination,int flowid){
    if (_hostfib.find(destination) == _hostfib.end() ||
        _hostfib[destination]->find(flowid) == _hostfib[destination]->end())
        return NULL;
    else {
        return (*_hostfib[destination])[flowid];
    }
}

void RouteTable::setRoutes(int destination, vector<FibEntry*>* routes){
    _fib[destination] = routes;
}
