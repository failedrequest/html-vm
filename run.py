"""Entry point: run html-vm under gevent + geventwebsocket.

WebSocket upgrades on /ws/serial/<name> and /ws/vnc/<name> require a server
that supports the websocket environ key.  Werkzeug's dev server does not.
Run as root:

    sudo .venv/bin/python run.py
"""
from gevent import monkey
monkey.patch_all()

from geventwebsocket.handler import WebSocketHandler
from gevent.pywsgi import WSGIServer

from app import app

def main():
    host_cfg = app.config.get("VM_HOST", "0.0.0.0:8088")
    host, _, port_s = host_cfg.partition(":")
    port = int(port_s) if port_s else 8088

    print(" * html-vm on http://{0}:{1}  (gevent + WebSocket)".format(host, port))
    server = WSGIServer((host, port), app, handler_class=WebSocketHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
