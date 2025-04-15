import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from nidaqmx import Task
from nidaqmx.constants import AcquisitionType
import time

# Configuration
device_name = "Dev1"
sample_rate = 10000  # samples per second per channel
channels = ["ai0", "ai1", "ai2", "ai3"]  # first four analog input channels
window_seconds = 5  # 5-second data window
buffer_size = sample_rate * window_seconds  # total samples to keep in memory

# Initialize data buffer
data_buffer = np.zeros((buffer_size, len(channels)))

# Create figure and axes
fig, ax = plt.subplots(len(channels), 1, figsize=(10, 8), sharex=True)
if len(channels) == 1:
    ax = [ax]  # Ensure ax is always a list for consistency

# Set up plots
lines = []
for i, ch in enumerate(channels):
    lines.append(ax[i].plot([], [], label=ch)[0])
    ax[i].set_ylabel(f'Channel {ch}\nVoltage (V)')
    ax[i].legend(loc='upper right')
    ax[i].grid(True)

ax[-1].set_xlabel('Time (s)')
fig.suptitle(f'Real-time DAQ Data from {device_name}')
plt.tight_layout()

# Time axis
time_axis = np.linspace(-window_seconds, 0, buffer_size)

# Initialize DAQ task
task = Task()
for ch in channels:
    task.ai_channels.add_ai_voltage_chan(f"{device_name}/{ch}")

task.timing.cfg_samp_clk_timing(
    rate=sample_rate,
    sample_mode=AcquisitionType.CONTINUOUS,
    samps_per_chan=buffer_size
)

# Start the task
task.start()

def update(frame):
    # Read new data
    new_data = task.read(number_of_samples_per_channel=sample_rate//10)  # read 100ms of data
    
    # Convert to numpy array if it isn't already
    new_data = np.array(new_data).T  # shape: (n_samples, n_channels)
    
    # Update data buffer (roll and replace)
    global data_buffer
    data_buffer = np.roll(data_buffer, -len(new_data), axis=0)
    data_buffer[-len(new_data):, :] = new_data
    
    # Update plots
    for i in range(len(channels)):
        lines[i].set_data(time_axis, data_buffer[:, i])
        ax[i].relim()
        ax[i].autoscale_view()
    
    return lines

# Create animation
ani = FuncAnimation(fig, update, interval=100, blit=True)  # update every 100ms

try:
    plt.show()
except KeyboardInterrupt:
    print("Stopping acquisition...")

# Clean up
task.stop()
task.close()
plt.close()
