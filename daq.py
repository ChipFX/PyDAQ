#!/usr/bin/env python3

import abc
import argparse
import datetime
import decimal
import json
import pint
import re
import rich
import rich.align
import rich.console
import rich.layout
import rich.live
import rich.panel
import rich.progress
import rich.table
import rich.traceback
import select
import serial
import serial.tools.list_ports
import socket
import sys
import time
import yaml

rich.traceback.install(show_locals=True)
console = rich.console.Console()
verbosity = 0

ureg=pint.UnitRegistry( non_int_type = decimal.Decimal )
ureg.formatter.default_format="~P"
ureg.define("@alias V = VDC")

def plog( lvl, *args, **kwargs ):
    if( verbosity >= lvl ):
        # XXX Add any nice logging stuff here, possibly one level for console and one for the file
        print(*args, **kwargs)

# Not everything is implemented (scpi stuff can be annoying) but I have tried to provide empty APIs to do so.
# I provide comments where things can be added for better handling of whatever
# Needs to add proper logging/tracing facilities too

class TextConnection(abc.ABC):
    """Base for connection wrappers.

    We use the wrapper so later we can use other than serial connections that may have a different API.
    """

    def __init__( self ):
        pass

    @abc.abstractmethod
    def write( self, strdata, timeout = None ):...
    """Writes some text data to the connection.

    String must not be terminated with any newline characters, the connection will do it automatically.
    """

    @abc.abstractmethod
    def readline( self, timeout = None ):...
    """Reads one line of text (until some newline char sequence) from the connection. Strips whitespace.

    Returns None if the timeout has triggered
    """



class SerialConnection(TextConnection):

    #def __init__(self, hwid, baudrate = 9600, bytesize = serial.EIGHTBITS, parity = serial.PARITY_NONE, stopbits = serial.STOPBITS_ONE, timeout = None, newline = "\n" ):
    def __init__(self, hwid, *, device = None, newline = "\n", **kwargs ):
        if( device is None ):
            # If other parametery to pyserials.Serial() are neceeary we can add them here
            # I recommend writing a convenience function that can parse strings like "9600,8N1"
            xx=serial.tools.list_ports.grep(hwid,True)
            xx=list(xx)
            if( len(xx) == 0 ):
                raise RuntimeError(f"No serial port matching hwid {hwid} found")
            for x in xx:
                # This might throw for permission denied or so
                con = serial.Serial( port = x.device, **kwargs )
                self.connection = con
                break
        else:
            con = serial.Serial( port = device, **kwargs )
            self.connection = con
        self.newline = newline.encode("utf-8")

    def write( self, strdata, timeout = None ):
        # XXX Need to handle timeout by setting it, then resetting it to old value, all exception safe (we should
        # probably write a contextmanager for that)
        if( isinstance(strdata,str) ):
            bindata = strdata.encode("utf-8")
        else:
            bindata = strdata

        self.connection.write(bindata)
        self.connection.write(self.newline)

    def readline( self, timeout = None ):
        # serial readline() always reads until \n so for the cases where line is ended with \r only this fails.
        ret = self.connection.readline()
#        print(f"SERIAL {ret=}")
        ret = ret.strip().decode("utf-8")
        return ret

class TCPConnection(TextConnection):

    def __init__(self, host, port ):
        self.socket = socket.create_connection( (host, port) )
        self.socket.setblocking(True)
        self.buffer = b""
        self.delim = b"\n"

    def write( self, strdata, timeout = None ):
        if( isinstance(strdata,str) ):
            bindata = strdata.encode("utf-8")
        else:
            bindata = strdata

        self.socket.send( bindata  + b"\n" )

    def readline( self, timeout = None ):
        nidx = self.buffer.find( self.delim )
        if( nidx != -1 ):
            nidx += 1
            rb = self.buffer[:nidx].decode(encoding="utf-8")
            self.buffer = self.buffer[nidx:]
            return rb.strip()
        # not enough in the buffer, wait for more
        ready = select.select( [self.socket],[],[],timeout)
        if( ready[0] ):
            self.buffer += self.socket.recv(4096)
            return self.readline(timeout)


class SCPI(abc.ABC):
    """
    Abstract base class for scpi devices. Should do all that weird scpi bit thing error handling stuff.
    """

    def __init__( self, connection ):
        self.connection = connection

        idn = self.query("*IDN?").split(",")
        self.manufacturer = idn[0]
        self.device_name = idn[1]
        self.version = idn[3]
        # Really what we want here is a proper mechanism that checks and sets all the bits, its a bit antiquated how
        # scpi handles this, thsis combination works for us now (needed for STB/ESR to properly return codes)
        self.send("*ESE 255")
        self.send(f"*SRE {0xff-32}")

    def check_error( self, cmd ):
#        self.query("*STB?",handle_errors = False)
#        self.query("*ESR?",handle_errors = False)
        ret = []
        while True:
            err = self.query("SYST:ERR?", handle_errors = False)
            if( err.startswith("+0") ):
                break
            ret.append(err)
        if( len(ret) > 0 ):
            raise RuntimeError(f"SCPI Error on '{cmd}': {','.join(ret)}")

    def query( self, cmd, *, handle_errors = True, dlb = False ):
        """
        Issues a query cmd and gathers the return. SCPI specs expect the cmd to contain a ? at the cmd end but we do not
        check this, the caller is reponsible
        """
        plog(3,f"=> {cmd}")
        self.connection.write(cmd)
        ret = self.connection.readline()
        if( dlb ):
            # Here is the thing, this DLB format is used quite looseley between devices, some use it for binary
            # transfer and then there is no \r\n after the message, for some it is. To support proper binary stuff we
            # really should not use the readline() but do the decoding on the fly
            # For now we just check if the len is correct
            if( not ret.startswith("#") ):
                raise RuntimeError(f"DLB Decode expected return to start with '#' but it is {ret[:16]}...")
            lenlen = int(ret[1])
            datalen = ret[2:2+lenlen]
            fulldatalen = int(datalen) + 2 + len(datalen)
#            print(f"{datalen=}")
#            print(f"{fulldatalen=}")
#            print(f"{len(ret)=}")
            if( fulldatalen != len(ret) ):
                raise RuntimeError(f"DLB Decode Error, received {len(ret)} but expected {fulldatalen}")
            ret = ret[-int(datalen):]
        plog(3,f"<= {ret}")
        if( handle_errors ):
            self.check_error(cmd)
        try:
            ret = int(ret)
        except ValueError:
            try:
                ret = decimal.Decimal(ret)
            except decimal.InvalidOperation:
                pass
        return ret

    def send( self, cmd, *, handle_errors = True ):
        """
        Sends some command (configuration ususally) without caring for a return. Should not contain a ? as otherwise the
        device sends something back.
        """
        plog(3,f"=> {cmd}")
        if( handle_errors ):
            self.check_error(cmd)
        self.connection.write(cmd)

    def query_or_send( self, cmd, *, handle_errors = True ):
        xcmd = cmd.split()[0]
        if( xcmd.endswith("?") ):
            return self.query(cmd,handle_errors = handle_errors )
        else:
            return self.send(cmd,handle_errors = handle_errors )

class DAQ(SCPI):
    def __init__( self, connection ):
        super().__init__(connection)

class _34970A(DAQ):

    MODULES = {
            "34907A" : ( 5 ),
            "34903A" : ( 20 ),
            "34902A" : ( 16 ),
            }
    def __init__( self, connection ):
        super().__init__(connection)
        if( self.device_name != "34970A" ):
            raise RuntimeError(f"Instantiated 34970A for {self.device_name}")
        self.check_error("init")
        self.configuration = {}

    def __str__( self ):
        # XXX Add modules too
        ret = f"{self.manufacturer} {self.device_name}"
        return ret

    def config( self, channel, cfg ):
        mode = cfg.get("mode")
        _range = cfg.get("range")
        resolution = cfg.get("resolution")
        nplc = cfg.get("nplc")

        if( mode is None ):
            currentmode = self.query(f"CONF? (@{channel})")
            mode = currentmode.split()[0]
            mode = mode.removeprefix('"')
        else:
            self.send(f"CONF:{mode} (@{channel})")
            # Need to do it twice for whatever reason
            self.check_error("??")

        if( _range is not None ):
            if( _range == "AUTO" ):
                self.send(f"{mode}:RANG:AUTO 1,(@{channel})")
            else:
                self.send(f"{mode}:RANG {_range},(@{channel})")
        if( resolution is not None ):
            self.send(f"{mode}:RES {resolution},(@{channel})")

        if( nplc is not None ):
            self.send(f"{mode}:NPLC {nplc} ,(@{channel})")

    def _get_datetime( self, tzone = None ):
        daq_date = self.query("SYST:DATE?")
        daq_time = self.query("SYST:TIME?")

        daq_y, daq_m, daq_d = daq_date.split(",")
        daq_hh, daq_mm, daq_secs = daq_time.split(",")

        daq_ss, daq_msec = daq_secs.split(".")
        daq_dt = datetime.datetime( int(daq_y), int(daq_m), int(daq_d), int(daq_hh), int(daq_mm), int(daq_ss), int(daq_msec) * 1000, tzinfo = tzone )

        return daq_dt


    # Syncs the time of the PC onto the device (you should make sure your PC time is accurate)
    def sync_time( self, dtime = None, utc = False ):
        if( dtime is None ):
            if( utc ):
                dtime = datetime.datetime.now(datetime.timezone.utc)
            else:
                dtime = datetime.datetime.now()

        tzone = None
        if( utc ):
            tzone = datetime.timezone.utc
        daq_dt = self._get_datetime(tzone=tzone)
        ts = daq_dt.timestamp()
        mets = dtime.timestamp()

        tdif = mets - ts

        tzn = "local"
        if( tzone is not None ):
            tzn = str(tzone)
        print(f"DAQ Synced to {tzn}, time difference was: {tdif}")

        dt_date = dtime.strftime(f"%Y,%m,%d")
        dt_time = dtime.strftime(f"%H,%M,%S.%f")

        self.send(f"SYST:DATE {dt_date}")
        self.send(f"SYST:TIME {dt_time}")

        # SYST:DATE yyyy,mm,dd
        # SYST:TIME hh,mm,ss.sss

    # Does one scan of everything and returns the data
    def scan( self, scanlist ):
        self.send(f"ROUT:SCAN (@{','.join(scanlist)})")

#        self.send( "CONF:VOLT:DC 10,0.003,(@203,208)" )
#        self.send( "CONF:VOLT:DC 10,0.003,(@208)" )
#        self.check_error("??")
#        self.query("*ESR?")
#        self.query("*ESR?")
#        self.send( "ROUT:SCAN (@203,208)" )
        self.send( "TRIG:SOUR IMM" )
        self.send("TRIG:COUN 1")
        self.query("SYST:TIME:SCAN?")
#        self.send( "INIT" )
#        ret = self.query("FETC?")
        ret = self.query("READ?")
        # XXX parse result and return in a proper way
        print(f"{ret=}")
        return ret

    # Generator that streams all the data
    def stream( self, scanlist, interval, count = 0 ):
        self.send(f"ROUT:SCAN (@{','.join(scanlist)})")
        self.send(f"TRIG:TIM {interval}")
        self.send("FORM:READ:TIME 1")
        self.send("FORM:READ:UNIT 1")
        self.send("TRIG:SOUR Timer")
        if( count ):
            self.send(f"TRIG:COUN {count}")
        else:
            self.send("TRIG:COUN INF")
        self.send("INIT")
#        self.query("SYST:TIME:SCAN?")
        yield from self._yield_scandata( scanlist, interval, count )

    def _yield_scandata( self, scanlist, interval, count = 0 ):
        num_reads = 0
        while True:
            numdata = self.query("DATA:POIN?")
            numdata = int(numdata)
            if( numdata < len(scanlist) ):
                time.sleep(float(interval/len(scanlist)))
                continue

            data = self.query(f"R? {len(scanlist)}",dlb=True)
            num_reads += 1
            vdata = data.split(",")
            rdata = {}
            for i,c in enumerate(scanlist):
                value = vdata[i*2]
                unit = None
                vv = value.split()
                if( len(vv) > 1 ):
                    value = vv[0]
                    unit = vv[1]
                tstamp = vdata[i*2+1]
                rdata[c] = (value,unit,tstamp)

            yield rdata
            if( count and num_reads >= count ):
                break

    # stops the current stream
    def abort( self ):
        self.send("ABORT")

    def status( self ):
        ret = self.query("STAT:OPER:COND?")
        return ret

    def resume( self ):
        stat = self.status()
        if( not self.is_scanning() ):
            raise RuntimeError("No scan to resume active")
        conf = self.query("ROUT:SCAN?",dlb=True)
        conf = conf.removeprefix("(@").removesuffix(")")
        scanlist = conf.split(",")
        interval = self.query("TRIG:TIM?")

        yield from self._yield_scandata( scanlist, interval )

    def is_scanning( self ):
        return self.status() & 16

    def get_modules( self ):
        ret = []
        for i in [ 100, 200, 300 ]:
            manufacturer,device,serial,version = self.query(f"SYST:CTYP? {i}").split(",")
            num_channels = self.MODULES.get( device )
            ret.append( (i,manufacturer,device,serial,version,num_channels) )
        return ret

    def show_modules( self ):
        tbl = rich.table.Table(title="Installed Modules")
        tbl.add_column( "Slot" )
        tbl.add_column( "Manufacturer" )
        tbl.add_column( "Model" )
        tbl.add_column( "Serial" )
        tbl.add_column( "Version" )
        tbl.add_column( "Channels" )

        for s,m,mod,ser,v,c in self.get_modules():
            tbl.add_row( str(s),m,mod,ser,v,str(c) )
        console.print(tbl)

    def get_channels( self ):
        ret = []
        for i in [ 100, 200, 300 ]:
            manufacturer,device,serial,version = self.query(f"SYST:CTYP? {i}").split(",")
            if( device not in self.MODULES ):
                raise RuntimeError(f"Module {device} in slot {i} unknown, please add configuration to the list of known modules")
            num_channels = self.MODULES.get( device )
            ret += list( range(i+1,i+num_channels+1) )
        return ret

# Encapsulates the whole scan settings etc. to separate it from the actual daq hardware (maybe one day we support a
# different one too)
class Scan:
    __NONE = object()
    def __init__( self, daq ):
        self.daq = daq
        self.known_channels = set( daq.get_channels() )
        self.channel_names = {}

    # Remove stuff from the configuration data tree so in the end we can tell that something was left we don't know
    # about
    def _consume( self, data, key, default = __NONE, valid = None ):
        ret = data.get(key,default)
        if( ret is self.__NONE ):
            raise KeyError(f"Required configuration key '{key}'")
        if( key in data ):
            del data[key]
        if( valid is not None ):
            if( ret not in valid ):
                raise ValueError(f"Invalid value {ret} for {key}, allowed keys {valid}")
        return ret

    def _consume_bool( self, data, key, default = __NONE ):
        ret = self._consume( data, key, default )
        if( isinstance(ret,bool) ):
            return ret
        oret = ret
        ret = ret.lower()
        if( ret in { "true", "on", "1", "yes" } ):
            ret = True
        elif( ret in { "false", "off", "0", "no" } ):
            ret = False
        else:
            raise ValueError(f"Invalid value for bool {oret}")
        return ret

    def _check_empty( self, data, msg ):
        if( len(data) == 0 ): return
        raise RuntimeError(f"Unknown configuration keys ({','.join(data.keys())}) {msg}")

    def _consume_cmd_list( self, data, key ):
        cmds = self._consume( data, key, [] )
        for i in cmds:
            if( not isinstance(i,str) ):
                raise ValueError(f"commands must be a list of strings, but {i} is not")
        return cmds

    def _run_cmds( self, cmdlist ):
        for i in cmdlist:
            self.daq.query_or_send( i )

    def config_channel( self, channel_id, channeldata ):
        # XXX This is quite a core configuration and should really be abstracted away and be in the actual DAQ
        # class(es). But for now this should suffice.
        # Check and save configuration, don't apply it yet
        if( channel_id not in self.known_channels ):
            raise KeyError(f"Channel {channel_id} is not found in known installed modules. Known channels: {','.join(map(str,self.known_channels))}")
        # XXX we should add something to the MODULES configuration to cross check with this one
        name = self._consume( channeldata, "name", None )
        resolution = self._consume( channeldata, "resolution", "DEF" )
        mode = self._consume( channeldata, "mode" )
        if( name is not None ):
            self.channel_names[channel_id] = name

        self.prepared_config[channel_id] = []
        match mode:
            case "VOLT:DC" | "VOLT:AC" | "CURR:AC" | "CURR:DC" :
                _range = self._consume( channeldata, "range", "AUTO" )
                if( _range == "AUTO" ):
                    if( resolution != "DEF" ):
                        raise RuntimeError("With range AUTO resolution must be DEF")
                conf_cmd = f"CONF:{mode} {_range},{resolution},(@{channel_id})"
                if( mode.endswith(":DC") ):
                    nplc = self._consume( channeldata, "nplc", None )
                    if( nplc is not None ):
                        self.prepared_config[channel_id].append( f"{mode}:NPLC {nplc},(@{channel_id})" )
#            case "DIG:BYTE":
            case "FREQ" | "PER":
                _range = self._consume( channeldata, "range", "AUTO" )
                resolution = self._consume( channeldata, "resolution", "DEF" )
                conf_cmd = f"CONF:{mode} {_range},{resolution},(@{channel_id})"
            case "FRES" | "RES":
                _range = self._consume( channeldata, "range", "AUTO" )
                resolution = self._consume( channeldata, "resolution", "DEF" )
                conf_cmd = f"CONF:{mode} {_range},{resolution},(@{channel_id})"
            case "TEMP":
                # No defaults, you need to specify it
                probe = self._consume( channeldata, "probe" )
                ptype = self._consume( channeldata, "type" )
                conf_cmd = f"CONF:{mode} {probe},{ptype},1,{resolution},(@{channel_id})"

            case "TOT":
                tmode = self._consume( channeldata, "tmode", "READ" )
                conf_cmd = f"CONF:{mode} {tmode},(@{channel_id})"
            case _:
                raise RuntimeError(f"Unsupported mode {mode}")

        # CONF must be the first one sine it resets all the others
        self.prepared_config[channel_id].insert(0, conf_cmd )

        # All the above checks valid values, if this channel is not enabled we simply remove it again but the checks are
        # done anyways
        enabled = self._consume_bool( channeldata, "enabled", True )
        if( not enabled ):
            del self.prepared_config[channel_id]
        # XXX Just for testing purposes
        self._check_empty( channeldata, f" for channel {channel_id}")

    def execute_config( self ):
        if( self.config_clock_sync == "local" ):
            self.daq.sync_time( utc = False)
        elif( self.config_clock_sync == "utc" ):
            self.daq.sync_time( utc = True)

        for channel_id,cmdlist in self.prepared_config.items():
            for cmd in cmdlist:
                self.daq.send(cmd)
                self.daq.check_error(f"Channel {channel_id}")


    # str : yml or json file (check file ending, default to yml), dict is the data itself (call us recursively?)
    def load( self, data ):
        print("Loading config...")
        # No (writing) access to self.daq allowed in here
        # Init commands must be all strings
        self.init_commands     = self._consume_cmd_list( data, "init" )
        self.setup_commands    = self._consume_cmd_list( data, "setup" )
        self.shutdown_commands = self._consume_cmd_list( data, "shutdown" )
        self.abort_commands    = self._consume_cmd_list( data, "abort" )
        self.config            = self._consume( data, "config" )
        self.scan              = self._consume( data, "scan" )

        self.config_timestamp  = self._consume( self.config, "timestamp", "offset", valid = { "offset", "single", "full" } )
        self.config_clock_sync = self._consume( self.config, "clock_sync", None )
        self.config_delimiter  = self._consume( self.config, "delimiter", ";" )
        self.config_output     = self._consume( self.config, "output" )
        self.config_unit       = self._consume( self.config, "with_unit", "none", valid = { "none", "inline", "separate" } )
        self.config_headers    = self._consume( self.config, "headers", [] )

        self.prepared_config = {}
        for module,channels in self._consume( data, "channels" ).items():
            for channel,channeldata in channels.items():
                channel_id = module + channel
                self.config_channel( channel_id, channeldata )

        self.scan_interval = float(self._consume( self.scan, "interval" ))
        self.scan_count = int( self._consume( self.scan, "count", 0 ) )
        self.scan_start = None
        self.csv_header = None

        self._check_empty( data, "" )
        self._check_empty( self.scan, "Scan config" )
        self._check_empty( self.config, "Global config" )

        if( verbosity > 2 ):
            console.print(data)

        if( verbosity > 1 ):
            console.print( self.prepared_config )
        print(f"Configured {len(self.prepared_config)} channels" )

    def date_to_timestamp(self, date_str):
        # 2025,04,23,22,23,42.068
        # Split the input string by comma for the first 6 components, then split again for milliseconds
        parts = date_str.split(',')

        if len(parts) != 6:
            raise ValueError("Input date string must be in 'YYYY,MM,DD,HH,MM,SS.mmm' format")

        # Extract individual components
        year, month, day, hour, minute = map(int,parts[:5])

        # Split the last component by dot to separate seconds and milliseconds
        second, milliseconds = map(int, parts[5].split('.'))

        # Create a datetime object
        dt = datetime.datetime(year, month, day, hour, minute, second )

        # Convert the datetime object to a timestamp
        timestamp = dt.timestamp()
        timestamp = decimal.Decimal(timestamp)
        timestamp += decimal.Decimal(milliseconds) / 1000

        return timestamp


    def write_reading( self, reading ):
        self.write_reading_csv( reading )


    def write_reading_csv( self, reading ):
        if( self.scan_start is None ):
            self.scan_start = self.daq.query("SYST:TIME:SCAN?")
            self.scan_start = self.date_to_timestamp(self.scan_start)

        d = self.config_delimiter
        if( self.csv_header is None ):
            header = "timestamp"
            for k in sorted(self.prepared_config.keys()):
                pc = self.prepared_config[k]
                cname = self.channel_names.get(k,k)
                header += f"{d}{cname}"
                if( self.config_unit == "separate" ):
                    header += f"{d}{cname}.unit"
                if( self.config_timestamp != "single" ):
                    header += f"{d}{cname}.timestamp"
#            print(f"{header=}")
            self.csv_header = header
            self.csv_file.write(header)
            self.csv_file.write("\n")
            for h in self.config_headers:
                self.csv_file.write(h)
                self.csv_file.write("\n")

        match( self.config_unit ):
            case "inline":
                unitfmt = " {rdata[1]}"
            case "none":
                unitfmt = ""
            case "separate":
                unitfmt = "{d}{rdata[1]}"

        fullts = None
        match( self.config_timestamp ):
            case "single":
                tsfmt = ""
            case "full":
                tsfmt = "{d}{fullts}"
                fullts = ""
            case "offset":
                tsfmt = "{d}{ts}"

        csvdata = ""
        row_time_offset = None
        for channel_id in sorted(self.prepared_config.keys()):
            channel_id = str(channel_id)
            rdata = reading[channel_id]
            if( row_time_offset is None ):
                row_time_offset = decimal.Decimal(rdata[2])
            ts = decimal.Decimal(rdata[2]) - row_time_offset
            if( fullts is not None ):
                fullts = f"{self.scan_start+decimal.Decimal(rdata[2])}"
            csvdata += f"{d}{rdata[0]}"
            csvdata += unitfmt.format(**locals())
            csvdata += tsfmt.format(**locals())
        self.csv_file.write(f"{self.scan_start+row_time_offset}")
        self.csv_file.write(csvdata)
        self.csv_file.write("\n")
        self.csv_file.flush()

    def _gen_display_table( self, hdata ):
        if( verbosity < 1 ):
            return ""
        if( len(hdata) > 0 ):
            headers = [ "Time" ]
            for k in hdata[0].keys():
                k = int(k)
                cname = self.channel_names.get(k,k)
                headers.append(cname)
                headers.append(f"{cname}.ts")
        else:
            headers = [ "Time", "??" ]
        table = rich.table.Table( *headers, title = "DAQ Data")
        for hd in hdata:
            rdata = []
            rowoffset = None
            for k,(v,u,t) in hd.items():
                t = decimal.Decimal(t)
                if( len(rdata) == 0 ):
                    rdata.append( f"{t}" )
                    rowoffset = t
                rdata.append( f"{v} {u}" )
                rdata.append( str(t - rowoffset) )
            table.add_row( *rdata )
        return table

    def run_scan( self, resume = False ):
        dtime = datetime.datetime.now()
        dstr = dtime.strftime(f"%Y.%m.%d %H.%M.%S.%f")
        self.csv_file_name = self.config_output.replace("{starttime}",dstr)
        self.csv_file = open(self.csv_file_name,"w")

        count = self.scan_count
        if( count == 0 ):
            count = None
        plist = [
                    rich.progress.TextColumn("[progress.description]{task.description}"),
                    rich.progress.MofNCompleteColumn(),
                    rich.progress.BarColumn(),
                    rich.progress.SpinnerColumn(),
                    rich.progress.TimeElapsedColumn(),
                    rich.progress.TimeRemainingColumn()
                 ]

        hdata = []

        table = self._gen_display_table(hdata)
        prog = rich.progress.Progress( *plist )
        group = rich.console.Group( table, prog )

        with rich.live.Live(group) as live:
            t1 = prog.add_task(f"Scanning {len(self.prepared_config)} channels", total = count )
            if( resume ):
                for d in self.daq.resume( ):
                    self.write_reading( d )
                    hdata.append(d)
                    hdata = hdata[-10:]
                    prog.update(t1,advance=1)
                    if( verbosity > 1 ):
                        prog.log(d)
                    table = self._gen_display_table(hdata)
                    group = rich.console.Group( table, prog )
                    live.update(group)
            else:
                for d in self.daq.stream( list( sorted( str(x) for x in self.prepared_config.keys() ) ), interval = self.scan_interval, count = self.scan_count ):
                    self.write_reading( d )
                    hdata.append(d.copy())
                    hdata = hdata[-10:]
                    prog.update(t1,advance=1)
                    if( verbosity > 1 ):
                        prog.log(d)
#                    prog.log(hdata)
                    table = self._gen_display_table(hdata)
                    group = rich.console.Group( table, prog )
                    live.update(group)
                    if( False ):
                        ld = d.popitem()
                        ld = d.popitem()
                        xd = ureg(f"{ld[1][0]} {ld[1][1]}")
                        xd = xd.m.normalize() * xd.u
                        xd = xd.to_compact()

                        self.daq.send(f'DISP:TEXT "{xd}"')


    def run( self, resume = False ):
        print("Running scan config")
        # XXX We need to save the result somehow, most useful would be a separate file to the main csv output to keep
        # the csv clean and easy to handle by e.g. excel
        if( not resume ):
            self._run_cmds( self.init_commands )
            self.execute_config( )
            self._run_cmds( self.setup_commands )
        self.run_scan(resume)
# XXX Can we somehow force the device to show the value in the display?

ydata = {
        # Cmds that are executed verbatim before almost anything else
        "init" : [
            "*IDN?",
            "FORM:READ:TIME 1"
            ],
        # Global configuration
        "config" : {
            "output" : "data.{starttime}.csv", # or "data.json"
            "timestamp" : "single", # single = one colum, offset = one column + one per reading, full: one column per result
            "with_unit" : "none", # "inline" = in the result column, "seperate" = in its own column
            "delimiter" : ";", # default = ;
            "clock_sync" : "utc", # or "local"
            },
        "channels" : {
            # This is split into 100+1 simply so that if modules are switched over there only needs to be one place with
            # changes
            300 : {
                # Channel 101
                1 : {
                    "name" : "Volt DUT", # used mainly for csv files or similar results
                    "range" : "AUTO",    #
                    "nplc" : 1,          # {mode}:NPLC
#                    "resolution" : 0.01,
                    "mode" : "VOLT:DC"
                    },
                2 : {
                    "name" : "Volt GUT", # used mainly for csv files or similar results
                    "range" : "AUTO",    #
                    "nplc" : 1,          # {mode}:NPLC
#                    "resolution" : 0.01,
                    "mode" : "VOLT:DC"
                    }
                }
            },
        "setup" : [
            "*IDN?"
            ],
        "scan" :
            {
                "interval" : "0.1", # in seconds, can be "external" for external trigger too.
            }
        }
ys = yaml.safe_dump(ydata)
#print(f"{ys}")

def autodetect( hwid, **kwargs ):

    devices=serial.tools.list_ports.grep(hwid,True)
    for device in devices:
        try:
            scon = SerialConnection(None,device=device.device, rtscts = True, **kwargs )
            daq = _34970A(scon)
            return daq
        except ( RuntimeError, serial.SerialException ):
            pass

bitmap = {
        "5" : serial.FIVEBITS,
        "6" : serial.SIXBITS,
        "7" : serial.SEVENBITS,
        "8" : serial.EIGHTBITS,
        }

paritymap = {
        "N" : serial.PARITY_NONE,
        "E" : serial.PARITY_EVEN,
        "O" : serial.PARITY_ODD,
        "M" : serial.PARITY_MARK,
        "S" : serial.PARITY_SPACE,
        }

stopbitmap = {
        "1" : serial.STOPBITS_ONE,
        "1.5" : serial.STOPBITS_ONE_POINT_FIVE,
        "2" : serial.STOPBITS_TWO
        }

def parse_serial_config( s ):
    nbits = s[0]
    parity = s[1]
    stopbits = s[2:]

    rbits = bitmap[nbits]
    rparity = paritymap[parity]
    rstopbits = stopbitmap[stopbits]

    return ( rbits, rparity, rstopbits )


def show_serial( ):
    tbl = rich.table.Table(title="Known Ports")
    tbl.add_column("device")
    tbl.add_column("hwid")
    tbl.add_column("description")
    tbl.add_column("vid")
    tbl.add_column("pid")
    tbl.add_column("serial")
    tbl.add_column("location")
    tbl.add_column("manufacturer")
    tbl.add_column("product")
    tbl.add_column("interface")
    for port in serial.tools.list_ports.grep(".*",True):
        portvid = None
        portpid = None

        if( port.vid is not None ):
            portvid = f"{port.vid:#06x}"
        if( port.pid is not None ):
            portpid = f"{port.pid:#06x}"
        tbl.add_row( str(port.device), str(port.hwid), str(port.description), str(portvid), str(portpid), str(port.serial_number), str(port.location), str(port.manufacturer), str(port.product), str(port.interface) )

    console.print(tbl)

    sys.exit(0)


def load_config_yaml( configfile ):
    with open(configfile) as f:
        ydata = yaml.safe_load(f)
        return ydata

def load_config_json( configfile ):
    with open(configfile) as f:
        jdata = json.load(f)
        return jdata

def load_config( configfile ):
    if( configfile.endswith(".yml") ):
        return load_config_yaml( configfile )
    else:
        return load_config_json( configfile )


def main( ):

    pre_parser = argparse.ArgumentParser(description='Operate DAQ', fromfile_prefix_chars="@", formatter_class=lambda prog: argparse.ArgumentDefaultsHelpFormatter(prog,max_help_position=42),add_help = False)
    pre_parser.add_argument("-a","--abort", action = "store_true" )
    pre_parser.add_argument("-S","--show", action = "store_true", help = "Show known serial devices" )
    pre_parser.add_argument("-t","--time",  action = "store", nargs = "?", const = "local", help = "Sync time of the device with the local host")
    pre_parser.add_argument("-v","--verbose", action = "count", help = "Verbosity" )

    args,_ = pre_parser.parse_known_args(sys.argv[1:])
    if( args.show ):
        show_serial()

    if( args.abort or args.time ):
        cnargs = "*"
    else:
        cnargs = 1

    if( args.verbose is not None ):
        global verbosity
        verbosity = args.verbose

    parser = argparse.ArgumentParser(description='Operate DAQ', fromfile_prefix_chars="@", formatter_class=lambda prog: argparse.ArgumentDefaultsHelpFormatter(prog,max_help_position=42), parents = [pre_parser])

    parser.add_argument("configfile", help = "configuration file (yml or json)", default = "", nargs = cnargs )
    parser.add_argument("-b","--baudrate", action = "store", default = 19200, help = "serial device baudrate" )
    parser.add_argument("-r","--resume", action = "store_true" )
    parser.add_argument("-s","--serial", action = "store", default = "8N1", help = "Serial configuration" )
    parser.add_argument("-H","--host", action = "store", help = "Host to connect to" )
    parser.add_argument("-P","--port", type = int, action = "store", help = "Port to connect to " )
    parser.add_argument("-d","--hwid", action = "store", default = ".*", help = "Serial device filter" )
    parser.add_argument("-n","--no-abort", action = "store_true", help = "Don't abort on ctrl-c")

    args = parser.parse_args(sys.argv[1:])
    bits,parity,stopbits = parse_serial_config( args.serial )

    if( args.host is not None ):
        if( args.port is None ):
            raise RuntimeError("Need to specify port" )
        con = TCPConnection( args.host, args.port )
        daq = _34970A(con)
    else:
        daq = autodetect( args.hwid, baudrate = args.baudrate, stopbits = stopbits, parity = parity, bytesize = bits )

    if( daq is None ):
        raise RuntimeError("Failed to determine daq connection")

    if( args.resume ):
        if( not daq.is_scanning() ):
            print("No active scan detected, cannot resume")
            sys.exit(1)

    if( args.abort ):
        daq.abort()
        print(f"Sent abort to daq {daq}")
        return

    if( args.time is not None ):
        match args.time:
            case "utc":
                daq.sync_time(utc=False)
            case "local":
                daq.sync_time(utc=False)
            case _:
                print(f"Invalid argument to -t: {args.time}")
        return
    scan = Scan(daq)
    cfgdata = load_config( args.configfile[0] )
#    print(f"{cfgdata=}")
#    cfgdata = ydata
    scan.load(cfgdata)
    try:
        daq.show_modules()
        scan.run(args.resume)
        daq.resume()
    except KeyboardInterrupt:
        time.sleep(0.1)
        if( not args.no_abort ):
            print(f"CTRL-C detected, trying to abort the {daq}")
            while True:
                try:
                    daq.abort()
                    break
                except RuntimeError as e:
                    print(f"Abort returned error: {e}")
                    time.sleep(0.1)

    print("Done")

if __name__ == "__main__":
    main()
    sys.exit(0)

# vim: tabstop=4 shiftwidth=4 expandtab ft=python
