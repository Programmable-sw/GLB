#include "rocepacket.h"

PacketDB<RocePacket> RocePacket::_packetdb;
PacketDB<RoceAck> RoceAck::_packetdb;
PacketDB<RoceNack> RoceNack::_packetdb;
PacketDB<RoceFastCnp> RoceFastCnp::_packetdb;
PacketDB<RoceEvProbe> RoceEvProbe::_packetdb;
