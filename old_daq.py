import sys, os
import numpy as np
from PyQt6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout,
                             QWidget, QPushButton, QHBoxLayout, QComboBox,
                             QCheckBox, QScrollArea)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
import pyqtgraph as pg
import nidaqmx
from threading import Thread, Event
import h5py
from datetime import datetime
import time
import json


class DAQController(QObject):
    toggle_logging_signal = pyqtSignal(bool)


class MultiChannelDAQ_GUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NI DAQ Multi-Channel Monitor")
        self.setGeometry(100, 100, 800, 600)

        # DAQ Configuration
        self.sample_rate = 10000  # 10 kHz
        self.update_interval = 100  # Plot update interval (ms)
        self.plot_window = 60.0  # Seconds of data to display
        self.buffer_size = int(self.sample_rate * self.plot_window)
        self.read_chunk_size = int(self.sample_rate * 0.1)  # Read 100ms at a time

        # Available channels
        self.all_channels = {
            "Dev1/ai0": "Behavior",
            "Dev1/ai1": "Microscope",
            "Dev1/ai2": "LickSensor",
            "Dev1/ai3": "DA"
        }

        # Start with first two channels enabled by default
        self.active_channels = ["Dev1/ai0", "Dev1/ai1"]
        self.data_buffers = {ch: np.zeros(self.buffer_size) for ch in self.active_channels}
        self.stop_event = Event()
        self.is_logging = False
        self.h5_file = None
        self.tab_orange_rgb = (255, 127, 14)  # Matplotlib's tab:orange in 0-255 range

        # GUI Setup
        self.init_ui()

        # Start DAQ thread immediately since we have default channels
        self.daq_thread = Thread(target=self.daq_worker, daemon=True)
        self.daq_thread.start()

        # Setup controller for external signals
        self.controller = DAQController()
        self.controller.toggle_logging_signal.connect(self.external_toggle_logging)

        # Start local server
        self.start_local_server()

    def init_ui(self):
        """Initialize GUI components."""
        main_widget = QWidget()
        layout = QVBoxLayout()

        # Channel selection area
        channel_select = QWidget()
        channel_layout = QHBoxLayout()

        # Checkboxes for each channel
        self.channel_checkboxes = []
        for ch_id, ch_name in self.all_channels.items():
            cb = QCheckBox(ch_name)
            # Set first two channels checked by default
            if ch_id in self.active_channels:
                cb.setChecked(True)
            cb.stateChanged.connect(self.update_active_channels)
            self.channel_checkboxes.append(cb)
            channel_layout.addWidget(cb)

        channel_select.setLayout(channel_layout)

        # Plot area (scrollable)
        self.plot_scroll = QScrollArea()
        self.plot_widget = QWidget()
        self.plot_layout = QVBoxLayout()
        self.plot_widget.setLayout(self.plot_layout)
        self.plot_scroll.setWidget(self.plot_widget)
        self.plot_scroll.setWidgetResizable(True)

        # Control buttons
        self.btn_log = QPushButton("Start HDF5 Logging")
        self.btn_log.clicked.connect(self.toggle_logging)
        self.btn_log.setEnabled(True)  # Enabled since we have default channels

        # Assemble main layout
        layout.addWidget(channel_select)
        layout.addWidget(self.plot_scroll)
        layout.addWidget(self.btn_log)
        main_widget.setLayout(layout)
        self.setCentralWidget(main_widget)

        # Create initial plots for default channels
        self.rebuild_plots()

    def start_local_server(self):
        socket_name = "DAQControlServer"
        # Unix-specific: Remove existing socket file if it exists
        if os.path.exists(f"/tmp/{socket_name}"):
            os.unlink(f"/tmp/{socket_name}")
        """Start a QLocalServer to listen for control commands"""
        self.server = QLocalServer()
        if not self.server.listen(f"/tmp/{socket_name}"):
            print("Failed to start local server:", self.server.errorString())
            return

        self.server.newConnection.connect(self.handle_new_connection)
        print("Local server started, waiting for connections...")

    def handle_new_connection(self):
        """Handle new incoming connections"""
        socket = self.server.nextPendingConnection()
        if not socket:
            return

        socket.readyRead.connect(lambda: self.process_command(socket))
        socket.disconnected.connect(socket.deleteLater)

    def process_command(self, socket):
        """Process incoming command from socket with robust error handling"""
        socket.setReadBufferSize(1024)  # Explicit buffer size

        # Initial data availability check
        if not socket.bytesAvailable():
            if not socket.waitForReadyRead(5000):  # Extended timeout
                print(f"No data after 5s (Buffers: {socket.bytesAvailable()} bytes)")
                socket.write(json.dumps({"status": "error", "message": "Connection timeout"}).encode())
                socket.flush()
                return False

        try:
            print(f"Processing command from {socket.serverName()} (State: {socket.state()})")

            # Secondary verification of data availability
            if not socket.waitForReadyRead(1000):
                error_msg = "No data received within timeout"
                print(f"Error: {error_msg}")
                socket.write(json.dumps({"status": "error", "message": error_msg}).encode())
                socket.flush()
                return False

            # Read and parse command
            raw_data = socket.readLine().data().decode().strip()
            print(f"Received raw command: {raw_data}")

            try:
                command = json.loads(raw_data)
                print(f"Parsed command: {command}")

                if command.get('action') == 'toggle_logging':
                    start = command.get('start', False)
                    print(f"Emitting toggle_logging_signal: {start}")
                    self.controller.toggle_logging_signal.emit(start)
                    response = {"status": "success", "action": "logging_toggled", "start": start}
                else:
                    response = {"status": "error", "message": "Unknown command"}

            except json.JSONDecodeError as e:
                response = {"status": "error", "message": f"Invalid JSON: {str(e)}"}

            # Send response
            response_str = json.dumps(response)
            print(f"Sending response: {response_str}")
            socket.write(response_str.encode())
            if not socket.waitForBytesWritten(1000):
                print(f"Warning: Failed to confirm response sent: {socket.errorString()}")
                return False

            return True

        except Exception as e:
            error_msg = f"Server error: {str(e)}"
            print(f"Critical error: {error_msg}")
            socket.write(json.dumps({"status": "error", "message": error_msg}).encode())
            return False
        finally:
            socket.flush()
            print("Command processing completed")

    def external_toggle_logging(self, start):
        """Handle external logging toggle requests"""
        if start and not self.is_logging:
            self.toggle_logging()
        elif not start and self.is_logging:
            self.toggle_logging()

    def update_active_channels(self):
        """Update which channels are active based on checkboxes."""
        self.active_channels = [
            ch_id for ch_id, ch_name in self.all_channels.items()
            if any(cb.isChecked() and cb.text() == ch_name
                   for cb in self.channel_checkboxes)
        ]

        # Reset data buffers
        self.data_buffers = {ch: np.zeros(self.buffer_size)
                             for ch in self.active_channels}

        # Rebuild plots
        self.rebuild_plots()

        # Start/restart DAQ thread if needed
        if self.active_channels:
            self.btn_log.setEnabled(True)
            if self.daq_thread and self.daq_thread.is_alive():
                self.stop_event.set()
                self.daq_thread.join()
            self.stop_event = Event()
            self.daq_thread = Thread(target=self.daq_worker, daemon=True)
            self.daq_thread.start()
        else:
            self.btn_log.setEnabled(False)

    def rebuild_plots(self):
        """Create/update plot widgets for active channels."""
        # Clear existing plots
        for i in reversed(range(self.plot_layout.count())):
            self.plot_layout.itemAt(i).widget().setParent(None)

        # Create new plots
        self.plot_curves = {}
        for ch in self.active_channels:
            plot = pg.PlotWidget(title=self.all_channels[ch])
            plot.setLabel('left', 'Voltage (V)')
            plot.setLabel('bottom', 'Time (s)')
            plot.setYRange(-10, 10)
            curve = plot.plot(pen=pg.mkPen(color=self.tab_orange_rgb))
            self.plot_curves[ch] = curve
            self.plot_layout.addWidget(plot)

    def daq_worker(self):
        """Thread to read DAQ data and update plots."""
        if not self.active_channels:
            return

        task = nidaqmx.Task()
        for ch in self.active_channels:
            task.ai_channels.add_ai_voltage_chan(ch)

        # Configure timing with a larger buffer
        task.timing.cfg_samp_clk_timing(
            rate=self.sample_rate,
            sample_mode=nidaqmx.constants.AcquisitionType.CONTINUOUS,
            samps_per_chan=self.sample_rate * 5  # 2-second buffer in hardware
        )
        task.start()

        try:
            while not self.stop_event.is_set():
                try:
                    # Read smaller chunks more frequently
                    new_data = task.read(
                        number_of_samples_per_channel=self.read_chunk_size,
                        timeout=2.0  # Timeout in seconds
                    )

                    # Handle single channel case (returns 1D array) vs multi-channel (returns list of arrays)
                    if len(self.active_channels) == 1:
                        new_data = [new_data]  # Convert to list of one array

                    # Update buffers
                    for i, ch in enumerate(self.active_channels):
                        self.data_buffers[ch] = np.roll(self.data_buffers[ch], -len(new_data[i]))
                        self.data_buffers[ch][-len(new_data[i]):] = new_data[i]

                    # Log to HDF5 (optimized)
                    if self.is_logging and self.h5_file:
                        timestamp = datetime.now().isoformat()
                        for i, ch in enumerate(self.active_channels):
                            # Get dataset references once
                            data_dset = self.h5_file[f"data/{self.all_channels[ch]}"]
                            time_dset = self.h5_file[f"timestamps/{self.all_channels[ch]}"]

                            # Calculate new size
                            new_length = data_dset.shape[0] + len(new_data[i])

                            # Resize datasets
                            data_dset.resize(new_length, axis=0)
                            time_dset.resize(new_length, axis=0)

                            # Store data
                            data_dset[-len(new_data[i]):] = new_data[i]
                            time_dset[-len(new_data[i]):] = timestamp

                except nidaqmx.DaqError as e:
                    if e.error_code == -200284:
                        print("Buffer overflow - consider increasing read frequency or buffer size")
                        continue
                    elif e.error_code == -200279:
                        print("Read position error - retrying...")
                        continue
                    raise

                time.sleep(self.update_interval / 1000)
        finally:
            task.stop()
            task.close()

    def toggle_logging(self):
        """Start/Stop HDF5 logging."""
        if not self.is_logging:
            # Initialize HDF5 file
            filename = f"daq_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.h5"
            self.h5_file = h5py.File(filename, 'w')

            # Create resizable datasets for all active channels
            for ch in self.active_channels:
                self.h5_file.create_dataset(
                    f"data/{self.all_channels[ch]}",
                    shape=(0,),
                    maxshape=(None,),
                    dtype=np.float64,
                    compression="gzip"
                )
                self.h5_file.create_dataset(
                    f"timestamps/{self.all_channels[ch]}",
                    shape=(0,),
                    maxshape=(None,),
                    dtype=h5py.string_dtype(encoding='utf-8'),
                    compression="gzip"
                )

            self.btn_log.setText("Stop Logging")
            self.is_logging = True
        else:
            # Close HDF5 file
            if self.h5_file:
                self.h5_file.close()
                self.h5_file = None
            self.btn_log.setText("Start Logging")
            self.is_logging = False

    def update_plots(self):
        """Update all active plots."""
        if not hasattr(self, 'plot_curves'):
            return

        time_axis = np.linspace(0, self.plot_window, self.buffer_size)
        for ch in self.active_channels:
            self.plot_curves[ch].setData(time_axis, self.data_buffers[ch])

    def closeEvent(self, event):
        """Cleanup on exit."""
        self.stop_event.set()
        if self.daq_thread and self.daq_thread.is_alive():
            self.daq_thread.join()
        if self.h5_file:
            self.h5_file.close()
        if hasattr(self, 'server') and self.server.isListening():
            self.server.close()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MultiChannelDAQ_GUI()
    window.show()

    # Timer for plot updates
    timer = pg.QtCore.QTimer()
    timer.timeout.connect(window.update_plots)
    timer.start(100)  # 100 ms refresh

    sys.exit(app.exec())