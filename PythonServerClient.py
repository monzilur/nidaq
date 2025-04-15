from multiprocessing.managers import BaseManager
import time


class SharedDataManager(BaseManager):
    pass


class PythonServer:
    def __init__(self):
        # This should be registered with the actual shared data class
        SharedDataManager.register('get_shared_data')
        self.manager = SharedDataManager(address=('localhost', 50000), authkey=b'AEPsecret')
        try:
            self.manager.connect()  # Attempt to connect to the server
            self.shared_data = self.manager.get_shared_data()  # Get the shared object
        except ConnectionRefusedError:
            print("Error: Could not connect to the server")
            self.shared_data = None
        except Exception as e:
            print(f"Unexpected error: {e}")
            self.shared_data = None

    def read_data(self, key):
        if self.shared_data is None:
            return None
        try:
            data = self.shared_data.get_data()  # Use self.shared_data
            if key in data:
                return data[key]
            else:
                return None
        except Exception as e:
            print(f"Error reading data: {e}")
            return None

    def write_data(self, key, value):
        if self.shared_data is None:
            return None
        try:
            if self.shared_data.update_data(key, value):
                print('Data written to python server successfully')
            else:
                print('Writing to python server was unsuccessful')
        except Exception as e:
            print(f"Error writing data: {e}")
            return None


    def close(self):
        """Properly clean up the connection"""
        if self.manager:
            # For BaseManager, we just need to clear references
            self.shared_data = None
            self.manager = None


def start_daq_recording(storePath=''):
    CC = PythonServer()
    CC.write_data('storePath', storePath)
    read_data = CC.read_data('storePath')
    print('StorePath send to DAQ recorder: ', read_data)

    CC.write_data('recording_command', False)
    time.sleep(1)
    CC.write_data('recording_command', True)
    read_data = CC.read_data('recording_command')
    print('Recording command: ', read_data)
    time.sleep(1)

    read_data = CC.read_data('recording')
    print('Recording state: ', read_data)
    CC.close()


def stop_daq_recording():
    CC = PythonServer()
    CC.write_data('recording_command', False)
    read_data = CC.read_data('recording_command')
    print('Recording command: ', read_data)
    time.sleep(5)
    CC.write_data('storePath', None)
    read_data = CC.read_data('recording')
    print('Recording state: ', read_data)
    CC.close()


if __name__ == '__main__':
    CC = PythonServer()
    CC.write_data('storePath', '/home/monzy/Log/mouse_00_test/03_27_2025/')

    read_data = CC.read_data('storePath')
    print('StorePath: ', read_data)

    while True:
        cmd = input('Enter command (s, e, q): ')
        if cmd == 's':
            CC.write_data('recording_command', True)
        elif cmd == 'e':
            CC.write_data('recording_command', False)
        elif cmd == 'q':
            break
        else:
            continue
        read_data = CC.read_data('recording')
        print('Recoding state: ', read_data)

        read_data = CC.read_data('recording_command')
        print('Recoding command: ', read_data)

    CC.close()