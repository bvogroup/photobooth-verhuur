import serial
import time

relay = serial.Serial('COM5', 9600)  # CH340 relay board

relay.write(b'\xA0\x01\x01\xA2')  # LED aan
time.sleep(2)
relay.write(b'\xA0\x01\x00\xA1')  # LED uit

relay.close()
