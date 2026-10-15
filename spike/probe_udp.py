"""Send Tidal-shaped /dirt/play bundles and a handshake to the spike."""
import socket, sys, time
from nanosynth.osc import OscBundle, OscMessage
port = int(sys.argv[1]) if len(sys.argv) > 1 else 57120
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2)
s.sendto(OscMessage("/dirt/handshake").to_datagram(), ("127.0.0.1", port))
reply = OscMessage.from_datagram(s.recvfrom(65536)[0])
print("reply", reply.address, len(reply.contents), "args")
t0 = time.time() + 0.25
for i, (snd, n, speed) in enumerate([("bd", 0, 1.0), ("sn", 1, 1.0), ("hh", 0, -1.0), ("arpy", 3, 1.5)]):
    m = OscMessage("/dirt/play", "cps", 0.5, "cycle", float(i), "delta", 0.5, "n", float(n), "s", snd, "speed", speed)
    s.sendto(OscBundle(timestamp=t0 + i * 0.25, contents=[m]).to_datagram(), ("127.0.0.1", port))
print("sent")
