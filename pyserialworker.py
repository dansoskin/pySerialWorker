# serialworker.py
from __future__ import annotations

import time
import threading
import queue
from dataclasses import dataclass
from typing import List, Optional

import serial
from serial.tools import list_ports


# ANSI color codes (same vibe as your C++)
RESET   = "\033[0m"
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"


def list_serial_ports(filter_str: str = "") -> List[str]:
    results: List[str] = []
    for p in list_ports.comports():
        # p.device, p.description, p.hwid
        print(f"Port: {p.device} | Description: {p.description} | Hardware ID: {p.hwid}\n")
        if not filter_str:
            results.append(p.device)
        else:
            haystack = f"{p.device} {p.description} {p.hwid}".lower()
            if filter_str.lower() in haystack:
                results.append(p.device)
    return results


@dataclass
class ConnectionParams:
    port: str = ""
    baudrate: int = 115200
    timeout_s: float = 10.0 


class SerialWorker:

    def __init__(self, debug_output: bool = False, debug_input: bool = False):
        self.debug_output = debug_output
        self.debug_input = debug_input

        self._params = ConnectionParams()
        self.sending_terminator = ""
        self.receiving_terminator = ""

        self._ser = None

        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._rx_queue = queue.Queue()
        self._read_buffer = ""  # accumulated decoded text

        # If your protocol is binary, you’ll want bytes parsing instead of str.
        self._encoding = "utf-8"
        self._decode_errors = "replace"

        # reconnect backoff
        self._reconnect_delay_s = 2.0

    # -------- public API --------

    def set_connection_params(self, port: str, baudrate: int) -> None:
        self._params.port = port
        self._params.baudrate = int(baudrate)

    def set_terminators(self, sending: str, receiving: str) -> None:
        self.sending_terminator = sending
        self.receiving_terminator = receiving

    def start(self) -> None:
        if self._running.is_set():
            print(f"{YELLOW}SerialWorker already running.{RESET}")
            return

        print(f"{GREEN}Starting SerialWorker...{RESET}")
        self._running.set()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="SerialWorkerThread", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        print(f"{GREEN}Stopping SerialWorker...{RESET}")
        self._running.clear()

        self._close()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None

    def sendData(self, data: str) -> None:
        if self._ser is None or not self._ser.is_open:
            print(f"{RED}Serial port not open!{RESET}")
            return

        payload = data + self.sending_terminator
        if self.debug_output:
            print(f"{CYAN}Serial >> {payload}{RESET}")

        try:
            self._ser.write(payload.encode(self._encoding, errors=self._decode_errors))
        except Exception as e:
            print(f"{RED}Serial write failed: {e}{RESET}")
            self._close()

    def get_data(self) -> str:
        """
        Non-blocking: returns "" if nothing available.
        """
        try:
            return self._rx_queue.get_nowait()
        except queue.Empty:
            return ""

    # -------- internal helpers --------

    def _connect(self) -> None:
        if not self._params.port:
            print(f"{RED}Port name is empty, cannot connect.{RESET}")
            return

        if self._ser is not None and self._ser.is_open:
            return

        try:
            self._ser = serial.Serial(
                port=self._params.port,
                baudrate=self._params.baudrate,
                timeout=self._params.timeout_s,   # read timeout (seconds)
                write_timeout=self._params.timeout_s,
            )
            print(f"{GREEN}Connected to {self._params.port}{RESET}")
        except Exception as e:
            if self._running.is_set():
                print(f"{RED}Connection failed: {e}{RESET}")
            self._ser = None

    def _close(self) -> None:
        try:
            if self._ser is not None and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass
        finally:
            self._ser = None

    def _loop(self) -> None:
        # Initial connect attempt
        self._connect()

        while self._running.is_set():
            if self._ser is None or not self._ser.is_open:
                time.sleep(self._reconnect_delay_s)
                if self._running.is_set():
                    self._connect()
                continue

            try:
                # pyserial: in_waiting is bytes available without blocking
                n = self._ser.in_waiting
                if n > 0:
                    raw = self._ser.read(n)  # returns bytes
                    chunk = raw.decode(self._encoding, errors=self._decode_errors)

                    if self.debug_input:
                        print(f"{GREEN}Serial << {chunk}{RESET}")

                    self._read_buffer += chunk

                    # Process complete messages based on receiving terminator
                    # Note: if receiving_terminator == "", this would loop forever.
                    if self.receiving_terminator:
                        term = self.receiving_terminator
                        while True:
                            pos = self._read_buffer.find(term)
                            if pos == -1:
                                break
                            message = self._read_buffer[:pos]
                            self._rx_queue.put(message)
                            self._read_buffer = self._read_buffer[pos + len(term):]
                else:
                    time.sleep(0.001)  # prevent CPU spinning (like your 1ms sleep)

            except Exception as e:
                if self._running.is_set():
                    print(f"Error reading serial (disconnecting): {e}")
                self._close()


# -------- minimal usage example --------
if __name__ == "__main__":
    ports = list_serial_ports()
    print("Ports:", ports)

    sw = SerialWorker(debug_output=True, debug_input=True)
    sw.set_connection_params(port=ports[0] if ports else "COM3", baudrate=115200)
    sw.set_terminators(sending="\n", receiving="\n")
    sw.start()

    sw.sendData("hello")

    t0 = time.time()
    while time.time() - t0 < 5:
        msg = sw.get_data()
        if msg:
            print("RX:", msg)
        time.sleep(0.05)

    sw.stop()
