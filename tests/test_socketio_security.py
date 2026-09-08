"""Bounded transport regressions for the two Socket.IO security advisories.

These tests use protocol connections, not administrator login. Each binary
message has only two one-byte attachments. No listener, client network socket,
project configuration, database, logger, or background worker is started.
"""

import unittest
from unittest import mock

import engineio
from engineio import packet as engineio_packet
from engineio.socket import Socket as EngineIOSocket
import socketio
from socketio import packet as socketio_packet


class SocketIOBinaryAttachmentSecurityTests(unittest.TestCase):
    def setUp(self):
        self.server = socketio.Server(
            async_mode="threading", async_handlers=False, monitor_clients=False,
        )
        send_packet_patcher = mock.patch.object(self.server, "_send_packet")
        self.send_packet = send_packet_patcher.start()
        self.addCleanup(send_packet_patcher.stop)
        self.eio_sid = "synthetic-engineio-session"
        self.server._handle_eio_connect(self.eio_sid, {})

    def _binary_parts(self, packet_type):
        data = [b"a", b"b"]
        if packet_type == socketio_packet.BINARY_EVENT:
            data.insert(0, "synthetic_event")
        base_type = (
            socketio_packet.EVENT
            if packet_type == socketio_packet.BINARY_EVENT else socketio_packet.ACK
        )
        packet = socketio_packet.Packet(base_type, data=data, id=1)
        self.assertEqual(packet.packet_type, packet_type)
        parts = packet.encode()
        self.assertEqual(len(parts), 3)
        return parts

    def _assert_unconnected_message_is_not_retained(self, packet_type):
        self.assertIsNone(self.server.manager.sid_from_eio_sid(self.eio_sid, "/"))
        header, _, _ = self._binary_parts(packet_type)
        try:
            self.server._handle_eio_message(self.eio_sid, header)
        except ValueError as error:
            self.assertEqual(str(error), "Unexpected binary packet")
        self.assertNotIn(self.eio_sid, self.server._binary_packet)

    def _assert_disconnect_cleans_partial_message(self, packet_type):
        # An accepted anonymous protocol connection is sufficient here.
        connect = socketio_packet.Packet(socketio_packet.CONNECT).encode()
        self.server._handle_eio_message(self.eio_sid, connect)
        sid = self.server.manager.sid_from_eio_sid(self.eio_sid, "/")
        self.assertIsNotNone(sid)
        self.assertTrue(self.server.manager.is_connected(sid, "/"))

        header, first_attachment, _ = self._binary_parts(packet_type)
        self.server._handle_eio_message(self.eio_sid, header)
        self.server._handle_eio_message(self.eio_sid, first_attachment)
        self.assertIn(self.eio_sid, self.server._binary_packet)
        pending = self.server._binary_packet[self.eio_sid]
        self.assertEqual(pending.attachments, [first_attachment])

        # Use Engine.IO's callback dispatch so both supported disconnect
        # signatures are exercised by the old/new dependency comparison.
        self.server.eio._trigger_event(
            "disconnect", self.eio_sid, "transport close", run_async=False,
        )
        self.assertFalse(self.server.manager.is_connected(sid, "/"))
        self.assertNotIn(self.eio_sid, self.server.environ)
        self.assertNotIn(self.eio_sid, self.server._binary_packet)

    def test_unconnected_binary_event_is_not_retained(self):
        self._assert_unconnected_message_is_not_retained(socketio_packet.BINARY_EVENT)

    def test_unconnected_binary_ack_is_not_retained(self):
        self._assert_unconnected_message_is_not_retained(socketio_packet.BINARY_ACK)

    def test_disconnect_cleans_partial_binary_event(self):
        self._assert_disconnect_cleans_partial_message(socketio_packet.BINARY_EVENT)

    def test_disconnect_cleans_partial_binary_ack(self):
        self._assert_disconnect_cleans_partial_message(socketio_packet.BINARY_ACK)


class EngineIOHeartbeatSecurityTests(unittest.TestCase):
    def test_repeated_pong_schedules_one_worker_per_ping_cycle(self):
        server = engineio.Server(async_mode="threading", monitor_clients=False)
        connection = EngineIOSocket(server, "synthetic-heartbeat-session")
        pong = engineio_packet.Packet(engineio_packet.PONG)
        with mock.patch.object(server, "start_background_task") as start_task:
            connection.last_ping = 1.0
            for _ in range(3):
                connection.receive(pong)
            start_task.assert_called_once_with(connection._send_ping)

            # A real subsequent ping must still allow the next heartbeat.
            connection.last_ping = 2.0
            connection.receive(pong)
            self.assertEqual(start_task.call_count, 2)


if __name__ == "__main__":
    unittest.main()
