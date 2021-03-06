# Simple L2 Switch for OF-DPA
#
# This application provides a L2 switching function based on OF-DPA switch.
#
# Author: TeYen(Danny) Liu
#
# Copyright (C) 2015 Gemini Open Cloud Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib import mac
#
from ofdpa.utils import Utils
from ofdpa.mods import Mods
#
import sys
#

class SimpleSwitchOFDPA(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    GROUP_ID = 0xa0001
    #FIXME:
    # I assume using vlan 10 because 
    # there is no any input information about vlan id
    MY_VLAN_ID = 10
    OFDPA_PRIORITY_ID = 1

    def __init__(self, *args, **kwargs):
        super(SimpleSwitchOFDPA, self).__init__(*args, **kwargs)
        self.mac_to_port = {}

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        
        #FIXME: Sending packet-in message to controller is probably not by this way. 
        # Based on the documenet about Source MAC Learning Feature, we need to use 
        # client_srcmac_learn program or others to this. Indigo doesn't provide this.
        self.add_flow(datapath, 0, match, actions, 
                      Utils.get_table("TABLE_ACL"))
        
    def add_flow(self, datapath, priority, match, actions, table_id):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]

        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst, 
                                table_id=table_id)
        datapath.send_msg(mod)
        
    def generate_group_id(self):
        GROUP_ID = GROUP_ID + 1
        return str(hex(GROUP_ID))

    def add_ofdpa_flow(self, datapath, priority, vlan_vid, dst, out_port):
        #prerequisite
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        #the group id will be generated by increment
        group_id = generate_group_id()
        
        #vlan table
        match = parser.OFPMatch(in_port=in_port, vlan_vid=vlan_vid)
        type = ofproto.OFPIT_GOTO_TABLE
        inst = [parser.OFPInstructionGotoTable(type, Utils.get_table("TABLE_MAC"))]
        mod = parser.OFPFlowMod(datapath=datapath,
                                cookie = 0,
                                cookie_mask = 0,
                                table_id=Utils.get_table("TABLE_VLAN"),
                                command = Utils.get_mod_command(datapath, "add"),
                                idle_timeout = 0,
                                hard_timeout = 0,
                                priority=priority,
                                buffer_id = 0,
                                match=match,
                                out_port = Utils.get_mod_port(datapath, "any"),
                                out_group = Utils.get_mod_group(datapath, "any"),
                                flags=0,
                                instructions=inst
                                )
        datapath.send_msg(mod)
        
        #bridging table
        match = parser.OFPMatch(eth_dst=dst, vlan_vid=vlan_vid)
        mask = mac.haddr_to_bin("ff:ff:ff:ff:ff:ff")
        match.set_dl_dst_masked(dst,mask)
        actions = [parser.OFPActionGroup(Utils.to_int(group_id))]
        type = ofproto.OFPIT_WRITE_ACTIONS
        inst = [parser.OFPInstructionActions(type, actions)]
        mod = parser.OFPFlowMod(datapath=datapath,
                                cookie = 0,
                                cookie_mask = 0,
                                table_id=Utils.get_table("TABLE_BRIDGING"),
                                command = Utils.get_mod_command(datapath, "add"),
                                idle_timeout = 0,
                                hard_timeout = 0,
                                priority=priority,
                                buffer_id = 0,
                                match=match,
                                out_port = Utils.get_mod_port(datapath, "any"),
                                out_group = Utils.get_mod_group(datapath, "any"),
                                flags=0,
                                instructions=inst
                                )
        datapath.send_msg(mod)
        
        #L2 Group table
        actions = [parser.OFPActionOutput(out_port)]
        buckets = []
        bucket = datapath.ofproto_parser.OFPBucket(
                     weight = Utils.to_int("0"),
                     watch_port = Utils.get_mod_port("any"),
                     watch_group = Utils.get_mod_group("any"),
                     actions = actions
                 )
        buckets.append(bucket)
        print "buckets: %s" % buckets

        mod = datapath.ofproto_parser.OFPGroupMod(
            datapath,
            Utils.get_mod_command(datapath, "add"),
            Utils.get_mod_type(datapath, "indirect"),
            Utils.get_mod_group(datapath, group_id),
            buckets
            )
        datapath.send_msg(mod)
    
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        dst = eth.dst
        src = eth.src

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        self.logger.info("packet in %s %s %s %s", dpid, src, dst, in_port)

        # learn a mac address to avoid FLOOD next time.
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        # install a flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            #add a bunch of flows into tables
            #based on bridging pipleline definition
            self.add_ofdpa_flow(datapath, OFDPA_PRIORITY_ID, 
                                MY_VLAN_ID, dst, out_port)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        actions = [parser.OFPActionOutput(out_port)]
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)
