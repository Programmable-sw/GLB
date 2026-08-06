#include "../datacenter/connection_matrix.h"

#include <cassert>
#include <sstream>

int main() {
    std::istringstream input(
        "Nodes 4\n"
        "Connections 2\n"
        "0->2 id 1 start 0 size 4096 lb ecmp\n"
        "1->3 id 2 start 1000 size 8192\n");
    ConnectionMatrix matrix(4);
    assert(matrix.load(input));
    std::vector<connection*>* connections = matrix.getAllConnections();
    assert(connections->size() == 2);
    assert(connections->at(0)->ecmp_override);
    assert(!connections->at(1)->ecmp_override);
    return 0;
}
