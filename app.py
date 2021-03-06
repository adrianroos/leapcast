#!/usr/bin/python

# Python program that emulates ChromeCast device

from twisted.internet import reactor
from twisted.internet.protocol import DatagramProtocol
import tornado.ioloop
import tornado.web
import tornado.websocket
import socket
import threading
import string
import argparse
import signal
import logging
from textwrap import dedent
import shlex
import subprocess
import json
import copy
import uuid

global_status = dict()
friendlyName = "Mopidy"
user_agent = "Mozilla/5.0 (CrKey - 0.9.3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/30.0.1573.2 Safari/537.36"
chrome = "/opt/google/chrome/chrome"
fullscreen = False


class SSDP(DatagramProtocol):
    SSDP_ADDR = '239.255.255.250'
    SSDP_PORT = 1900
    MS = """HTTP/1.1 200 OK\r
LOCATION: http://$ip:8008/ssdp/device-desc.xml\r
CACHE-CONTROL: max-age=1800\r
CONFIGID.UPNP.ORG: 7337\r
BOOTID.UPNP.ORG: 7337\r
USN: uuid:$uuid\r
ST: urn:dial-multiscreen-org:service:dial:1\r
\r
"""

    def __init__(self, iface):
        self.iface = iface
        self.transport = reactor.listenMulticast(
            self.SSDP_PORT, self, listenMultiple=True)
        self.transport.setLoopbackMode(1)
        self.transport.joinGroup(self.SSDP_ADDR, interface=iface)

    def stop(self):
        self.transport.leaveGroup(self.SSDP_ADDR, interface=self.iface)
        self.transport.stopListening()

    def datagramReceived(self, datagram, address):
        if "urn:dial-multiscreen-org:service:dial:1" in datagram and "M-SEARCH" in datagram:
            iface = self.iface
            if not iface:
                # Create a socket to determine what address the client should
                # use
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(address)
                iface = s.getsockname()[0]
                s.close()
            data = string.Template(dedent(self.MS)).substitute(
                ip=iface, uuid=uuid.uuid5(uuid.NAMESPACE_DNS, friendlyName))
            self.transport.write(data, address)


class LEAP(tornado.web.RequestHandler):
    application_status = dict(
        name="",
        state="stopped",
        link="",
        pid=None,
        connectionSvcURL="",
        protocols="",
    )
    service = """<?xml version="1.0" encoding="UTF-8"?>
    <service xmlns="urn:dial-multiscreen-org:schemas:dial">
        <name>$name</name>
        <options allowStop="true"/>
        <activity-status xmlns="urn:chrome.google.com:cast">
            <description>Legacy</description>
        </activity-status>
        <servicedata xmlns="urn:chrome.google.com:cast">
            <connectionSvcURL>$connectionSvcURL</connectionSvcURL>
            <protocols>$protocols</protocols>
        </servicedata>
        <state>$state</state>
        $link
    </service>
    """

    ip = None
    url = "$query"
    protocols = ""

    def get_name(self):
        return self.__class__.__name__

    def get_status_dict(self):
        status = copy.deepcopy(self.application_status)
        status["name"] = self.get_name()
        return status

    def prepare(self):
        self.ip = self.request.host

    def get_app_status(self):
        return global_status.get(self.get_name(), self.get_status_dict())

    def set_app_status(self, app_status):
        global global_status
        app_status["name"] = self.get_name()
        global_status[self.get_name()] = app_status

    def _response(self):
        self.set_header("Content-Type", "application/xml")
        self.set_header(
            "Access-Control-Allow-Method", "GET, POST, DELETE, OPTIONS")
        self.set_header("Access-Control-Expose-Headers", "Location")
        self.set_header("Cache-control", "no-cache, must-revalidate, no-store")
        self.finish(self._toXML(self.get_app_status()))

    @tornado.web.asynchronous
    def post(self, sec):
        """Start app"""
        self.clear()
        self.set_status(201)
        self.set_header("Location", self._getLocation(self.get_name()))

        status = self.get_status_dict()
        status["state"] = "running"
        status["link"] = """<link rel="run" href="web-1"/>"""
        status["pid"] = self.launch(self.request.body)
        status["connectionSvcURL"] = "http://%s/connection/%s" % (
            self.ip, self.get_name())
        status["protocols"] = self.protocols

        self.set_app_status(status)
        self.finish()

    @tornado.web.asynchronous
    def get(self, sec):
        """Status of an app"""
        self.clear()
        if self.get_app_status()["pid"]:
            # app crashed or closed
            if self.get_app_status()["pid"].poll() is not None:

                status = self.get_status_dict()
                status["state"] = "stopped"
                status["link"] = ""
                status["pid"] = None

                self.set_app_status(status)
        self._response()

    @tornado.web.asynchronous
    def delete(self, sec):
        """Close app"""
        self.clear()
        self.destroy(self.get_app_status()["pid"])
        status = self.get_status_dict()
        status["state"] = "stopped"
        status["link"] = ""
        status["pid"] = None

        self.set_app_status(status)
        self._response()

    def _getLocation(self, app):
        return "http://%s/apps/%s/web-1" % (self.ip, app)

    def launch(self, data):
        appurl = string.Template(self.url).substitute(query=data)
        if not fullscreen:
            appurl = '--app="%s"' % appurl
        command_line = """%s --incognito --kiosk --user-agent="%s"  %s"""  % (
            chrome, user_agent, appurl)
        args = shlex.split(command_line)
        return subprocess.Popen(args)

    def destroy(self, pid):
        if pid is not None:
            pid.terminate()

    def _toXML(self, data):
        return string.Template(dedent(self.service)).substitute(data)

    @classmethod
    def toInfo(cls):
        data = copy.deepcopy(cls.application_status)
        data["name"] = cls.__name__
        data = global_status.get(cls.__name__, data)
        return string.Template(dedent(cls.service)).substitute(data)


class ChromeCast(LEAP):
    url = "https://www.gstatic.com/cv/receiver.html?$query"
    protocols = "<protocol>ramp</protocol>"


class YouTube(LEAP):
    url = "https://www.youtube.com/tv?$query"
    protocols = "<protocol>ramp</protocol>"


class PlayMovies(LEAP):
    url = "https://play.google.com/video/avi/eureka?$query"
    protocols = "<protocol>ramp</protocol><protocol>play-movies</protocol>"


class GoogleMusic(LEAP):
    url = "https://play.google.com/music/cast/player"
    protocols = "<protocol>ramp</protocol>"


class GoogleCastSampleApp(LEAP):
    url = "http://anzymrcvr.appspot.com/receiver/anzymrcvr.html"
    protocols = "<protocol>ramp</protocol>"


class GoogleCastPlayer(LEAP):
    url = "https://www.gstatic.com/eureka/html/gcp.html"
    protocols = "<protocol>ramp</protocol>"


class Fling(LEAP):
    url = "https://www.gstatic.com/eureka/html/gcp.html"
    protocols = "<protocol>ramp</protocol>"


class TicTacToe(LEAP):
    url = "http://www.gstatic.com/eureka/sample/tictactoe/tictactoe.html"
    protocols = "<protocol>com.google.chromecast.demo.tictactoe</protocol>"


class DeviceHandler(tornado.web.RequestHandler):

    device = """<?xml version="1.0" encoding="utf-8"?>
    <root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:r="urn:restful-tv-org:schemas:upnp-dd">
        <specVersion>
        <major>1</major>
        <minor>0</minor>
        </specVersion>
        <URLBase>$path</URLBase>
        <device>
            <deviceType>urn:schemas-upnp-org:device:dail:1</deviceType>
            <friendlyName>$friendlyName</friendlyName>
            <manufacturer>Google Inc.</manufacturer>
            <modelName>Eureka Dongle</modelName>
            <UDN>uuid:$uuid</UDN>
            <serviceList>
                <service>
                    <serviceType>urn:schemas-upnp-org:service:dail:1</serviceType>
                    <serviceId>urn:upnp-org:serviceId:dail</serviceId>
                    <controlURL>/ssdp/notfound</controlURL>
                    <eventSubURL>/ssdp/notfound</eventSubURL>
                    <SCPDURL>/ssdp/notfound</SCPDURL>
                </service>
            </serviceList>
        </device>
    </root>"""

    def get(self):
        if self.request.uri == "/apps":
            for app, astatus in global_status.items():
                if astatus["state"] == "running":
                    self.redirect("/apps/%s" % app)
            self.set_status(204)
            self.set_header(
                "Access-Control-Allow-Method", "GET, POST, DELETE, OPTIONS")
            self.set_header("Access-Control-Expose-Headers", "Location")
            self.finish()
        else:
            self.set_header(
                "Access-Control-Allow-Method", "GET, POST, DELETE, OPTIONS")
            self.set_header("Access-Control-Expose-Headers", "Location")
            self.add_header(
                "Application-URL", "http://%s/apps" % self.request.host)
            self.set_header("Content-Type", "application/xml")
            self.write(string.Template(dedent(self.device)).substitute(
                dict(
                    uuid=uuid.uuid5(uuid.NAMESPACE_DNS, friendlyName),
                    friendlyName=friendlyName,
                    path="http://%s" % self.request.host)
            )
            )


class WS(tornado.websocket.WebSocketHandler):

    def open(self, app=None):
        self.app = app
        logging.info("%s opened %s" %
                     (self.__class__.__name__, self.request.uri))
        self.cmd_id = 0

    def on_message(self, message):
        cmd = json.loads(message)
        self.on_cmd(cmd)

    def on_cmd(self, cmd):
        print(cmd)

    def on_close(self):
        logging.info("%s closed %s" %
                     (self.__class__.__name__, self.request.uri))

    def reply(self, msg):
        msg["cmd_id"] = self.cmd_id
        self.write_message((json.dumps(msg)))
        self.cmd_id += 1


class CastChannel(WS):

    """
    RAMP over WebSocket.  It acts like proxy between receiver app(1st screen) and remote app(2nd screen)
    """

    def on_cmd(self, cmd):
        if cmd["type"] == "REGISTER":
            self.info = cmd
            self.new_request()
        if cmd["type"] == "CHANNELRESPONSE":
            self.new_chanell()

    def new_chanell(self):
        ws = "ws://localhost:8008/connection/%s" % self.info["name"]
        logging.info("New channel for app %s %s" % (self.info["name"], ws))
        self.reply(
            {"type": "NEWCHANNEL", "senderId": "1", "requestId": "123456", "URL": ws})

    def new_request(self):
        logging.info("New CHANNELREQUEST for app %s" % (self.info["name"]))
        self.reply(
            {"type": "CHANNELREQUEST", "requestId": "123456", "senderId": "1"})


class CastPlatform(WS):

    """
    Remote control over WebSocket.

    Commands are:
    {u'type': u'GET_VOLUME', u'cmd_id': 1}
    {u'type': u'GET_MUTED', u'cmd_id': 2}
    {u'type': u'VOLUME_CHANGED', u'cmd_id': 3}
    {u'type': u'SET_VOLUME', u'cmd_id': 4}
    {u'type': u'SET_MUTED', u'cmd_id': 5}

    Device control:

    """


class CastRAMP(WS):

    """
    Remote proxy over WebSocket.

    """

    def reply(self, msg):
        self.write_message((json.dumps(msg)))

    def on_cmd(self, cmd):
        if cmd[0] == "cm":
            self.on_cm_command(cmd[1])
        if cmd[0] == "ramp":
            self.on_ramp_command(cmd[1])

    def on_cm_command(self, cmd):
        print cmd
        self.reply(['cm', {'type': 'pong'}])

    def on_ramp_command(self, cmd):
        print cmd


class HTTPThread(object):

    def __init__(self, iface):
        self.iface = iface

    def register_app(self, app):
        name = app.__name__
        return (r"(/apps/" + name + "|/apps/" + name + "/run)", app)

    def run(self):

        self.application = tornado.web.Application([
            (r"/ssdp/device-desc.xml", DeviceHandler),
            (r"/apps", DeviceHandler),

            self.register_app(ChromeCast),
            self.register_app(YouTube),
            self.register_app(PlayMovies),
            self.register_app(GoogleMusic),
            self.register_app(GoogleCastSampleApp),
            self.register_app(GoogleCastPlayer),
            self.register_app(TicTacToe),
            self.register_app(Fling),

            (r"/connection", CastChannel),
            (r"/connection/([^\/]+)", CastRAMP),
            (r"/system/control", CastPlatform),
        ])
        self.application.listen(8008, address=self.iface)
        tornado.ioloop.IOLoop.instance().start()

    def start(self):
        threading.Thread(target=self.run).start()

    def shutdown(self, ):
        logging.info('Stopping HTTP server')
        reactor.callFromThread(reactor.stop)
        logging.info('Stopping DIAL server')
        tornado.ioloop.IOLoop.instance().stop()

    def sig_handler(self, sig, frame):
        tornado.ioloop.IOLoop.instance().add_callback(self.shutdown)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--iface', help='Interface you want to bind to (for example 192.168.1.22)', default='')
    parser.add_argument('--name', help='Friendly name for this device')
    parser.add_argument('--user_agent', help='Custom user agent')
    parser.add_argument('--chrome', help='Path to Google Chrome executable')
    parser.add_argument('--fullscreen', action='store_true',
                        default=False, help='Start in full-screen mode')
    args = parser.parse_args()

    if args.name:
        friendlyName = args.name
        logging.info("Service name is %s" % friendlyName)

    if args.user_agent:
        user_agent = args.user_agent
        logging.info("User agent is %s" % user_agent)

    if args.chrome:
        chrome = args.chrome
        logging.info("Chrome path is %s" % chrome)

    if args.fullscreen:
        fullscreen = True

    server = HTTPThread(args.iface)
    server.start()

    signal.signal(signal.SIGTERM, server.sig_handler)
    signal.signal(signal.SIGINT, server.sig_handler)

    def LeapUPNPServer():
        logging.info("Listening on %s" % (args.iface or 'all'))
        sobj = SSDP(args.iface)
        reactor.addSystemEventTrigger('before', 'shutdown', sobj.stop)

    reactor.callWhenRunning(LeapUPNPServer)
    reactor.run()
