import sys, os
import numpy as np
from PyQt6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout,
                             QWidget, QPushButton, QHBoxLayout, QComboBox,
                             QCheckBox, QScrollArea)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
import pyqtgraph as pg
import nidaqmx
from threading import Thread, Event
import h5py
from datetime import datetime
import time
import json
import threading
from PythonServerClient import PythonServer

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
            "Dev1/ai5": "Microscope",
            "Dev1/ai2": "LickSensor",
            "Dev1/ai3": "DA"
        }

        # Start with first two channels enabled by default
        self.active_channels = ["Dev1/ai0", "Dev1/ai5"]
        self.data_buffers = {ch: np.zeros(self.buffer_size) for ch in self.active_channels}
        # In your initialization code (__init__ or setup method)
        self.dtype = np.float64  # Centralize your data type definition
        # self.data_buffers = {
        #     ch: np.zeros(self.buffer_size, dtype=self.dtype)
        #     for ch in self.active_channels
        # }
        self.stop_event = Event()
        self.is_logging = False
        self.h5_file = None
        self.tab_orange_rgb = (255, 127, 14)  # Matplotlib's tab:orange in 0-255 range

        # GUI Setup
        self.init_ui()

        # Start DAQ thread immediately since we have default channels
        self.daq_thread = Thread(target=self.daq_worker, daemon=True)
        self.daq_thread.start()

        # Setup multiprocessing recording flag
        self.server = PythonServer()
        self.server.write_data('recording', 0)
        self.start_logging_monitor()

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

    def start_logging_monitor(self):
        """Start a thread to monitor and toggle logging periodically."""
        self._monitor_running = True

        def monitor_loop():
            while self._monitor_running:
                self.external_toggle_logging()
                time.sleep(1)

        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop_logging_monitor(self):
        """Stop the logging monitor thread."""
        self._monitor_running = False
        if hasattr(self, '_monitor_thread'):
            self._monitor_thread.join(timeout=2)  # Wait up to 2 seconds for thread to finish

    def external_toggle_logging(self):
        recording_command = self.server.read_data('recording_command')
        print('Recording ON', recording_command, self.is_logging)
        if recording_command is not None:
            if recording_command and not self.is_logging:
                self.toggle_logging()
                self.server.write_data('recording_command', None)
            elif not recording_command and self.is_logging:
                self.toggle_logging()
                self.server.write_data('recording_command', None)

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
        """Thread to read DAQ data and update plots and logging with robust HDF5 handling."""
        if not self.active_channels:
            return

        task = nidaqmx.Task()
        for ch in self.active_channels:
            task.ai_channels.add_ai_voltage_chan(ch)

        # Configure timing with buffer
        task.timing.cfg_samp_clk_timing(
            rate=self.sample_rate,
            sample_mode=nidaqmx.constants.AcquisitionType.CONTINUOUS,
            samps_per_chan=self.sample_rate * 5
        )
        task.start()

        try:
            while not self.stop_event.is_set():
                try:
                    # Read data chunk
                    new_data = task.read(
                        number_of_samples_per_channel=self.read_chunk_size,
                        timeout=2.0
                    )

                    # Ensure data is in list of arrays format
                    if len(self.active_channels) == 1:
                        new_data = [new_data]

                    # Convert all channels to numpy arrays with explicit float32 dtype
                    new_data = [np.asarray(ch_data, dtype=np.float32) for ch_data in new_data]

                    # Update in-memory buffers
                    for i, ch in enumerate(self.active_channels):
                        self.data_buffers[ch] = np.roll(self.data_buffers[ch], -len(new_data[i]))
                        self.data_buffers[ch][-len(new_data[i]):] = new_data[i]

                    # Log to HDF5 if enabled
                    if self.is_logging and self.h5_file:
                        timestamp = datetime.now().isoformat()
                        for i, ch in enumerate(self.active_channels):
                            channel_name = self.all_channels[ch]
                            data_path = f"data/{channel_name}"
                            time_path = f"timestamps/{channel_name}"

                            try:
                                # Skip if no data
                                if len(new_data[i]) <= 0:
                                    continue

                                # Initialize flag for dataset recreation
                                recreate_datasets = False

                                # Check existing datasets
                                if data_path in self.h5_file:
                                    try:
                                        # Test if dataset is accessible
                                        test = self.h5_file[data_path].dtype
                                    except:
                                        recreate_datasets = True
                                else:
                                    recreate_datasets = True

                                if recreate_datasets:
                                    # Safely remove existing datasets if they exist
                                    for path in [data_path, time_path]:
                                        if path in self.h5_file:
                                            try:
                                                del self.h5_file[path]
                                            except:
                                                pass

                                    # Create new datasets with explicit simple types
                                    self.h5_file.create_dataset(
                                        data_path,
                                        data=new_data[i],
                                        maxshape=(None,),
                                        dtype='float32',  # Simple type specification
                                        chunks=(min(1000, len(new_data[i])),),
                                        compression="gzip"
                                    )
                                    self.h5_file.create_dataset(
                                        time_path,
                                        data=np.array([timestamp] * len(new_data[i]), dtype=h5py.string_dtype()),
                                        maxshape=(None,),
                                        chunks=(min(1000, len(new_data[i])),),
                                        compression="gzip"
                                    )
                                else:
                                    # Access existing datasets
                                    data_dset = self.h5_file[data_path]
                                    time_dset = self.h5_file[time_path]

                                    # Resize datasets
                                    new_length = data_dset.shape[0] + len(new_data[i])
                                    data_dset.resize(new_length, axis=0)
                                    time_dset.resize(new_length, axis=0)

                                    # Write data with explicit type conversion
                                    write_data = np.array(new_data[i], dtype=data_dset.dtype)
                                    data_dset[-len(write_data):] = write_data
                                    time_dset[-len(write_data):] = np.array([timestamp] * len(write_data),
                                                                            dtype=h5py.string_dtype())

                            except Exception as e:
                                print(f"Error processing channel {ch}: {str(e)}")
                                continue

                except nidaqmx.DaqError as e:
                    if e.error_code == -200284:
                        continue  # Buffer overflow
                    elif e.error_code == -200279:
                        continue  # Read position error
                    print(f"DAQ Error: {str(e)}")
                    continue

                except Exception as e:
                    print(f"Unexpected error: {str(e)}")
                    continue

                time.sleep(self.update_interval / 1000)

        finally:
            try:
                task.stop()
                task.close()
            except:
                pass
            if self.is_logging and self.h5_file:
                try:
                    self.h5_file.flush()
                except:
                    pass
            print("DAQ task stopped and closed")

    def daq_worker_old(self):
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
                    print(f"Error occurred while processing index {i}, data: {new_data[i]}")
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
            if self.server.read_data('storePath') is not None:
                filename = self.server.read_data('storePath') + filename

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
            self.server.write_data('recording', 1)  # Update shared value
        else:
            # Close HDF5 file
            if self.h5_file:
                self.h5_file.close()
                self.h5_file = None
            self.btn_log.setText("Start Logging")
            self.is_logging = False
            self.server.write_data('recording', 0)  # Update shared value

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
        if hasattr(self, 'process') and self.process.is_alive():
            self.process.terminate()
        event.accept()
        self.server.close()
        self.stop_logging_monitor()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MultiChannelDAQ_GUI()
    window.show()

    # Timer for plot updates
    timer = pg.QtCore.QTimer()
    timer.timeout.connect(window.update_plots)
    timer.start(100)  # 100 ms refresh

    sys.exit(app.exec())