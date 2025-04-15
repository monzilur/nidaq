from multiprocessing.managers import BaseManager
from threading import RLock
import sys


class SharedData:
    def __init__(self):
        self._data = {}
        self._lock = RLock()  # Reentrant lock for all operations

    def _get_size(self, obj):
        """Calculate approximate memory usage of an object in bytes"""
        size = sys.getsizeof(obj)
        if isinstance(obj, dict):
            size += sum(self._get_size(k) + self._get_size(v) for k, v in obj.items())
        elif isinstance(obj, (list, tuple, set)):
            size += sum(self._get_size(x) for x in obj)
        return size

    def get_data(self):
        """Read-protected method that returns a copy of the data"""
        with self._lock:
            return self._data.copy()

    def update_data(self, key, value):
        # Calculate size of new value without lock
        value_size = self._get_size(value)
        if value_size >= 25 * 1024 * 1024:  # 25 MB
            print(f"Error: Value size {value_size / 1024 / 1024:.2f} MB exceeds 25 MB limit")
            return False

        with self._lock:
            # Check total size under lock to prevent race condition
            current_size = self._get_size(self._data)
            if current_size >= 50 * 1024 * 1024:
                print(
                    f"Error: Total data size changed to {current_size / 1024 / 1024:.2f} MB while checking, exceeds 50 MB limit")
                return False

            # If we get here, both conditions are satisfied
            self._data[key] = value
            return True

    def delete_data(self, key):
        """Write-protected method to delete data"""
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

if __name__ == '__main__':
    shared_data = SharedData()

    class SharedDataManager(BaseManager):
        pass

    # Only expose the data-related methods
    SharedDataManager.register('get_shared_data', callable=lambda: shared_data)

    manager = SharedDataManager(address=('localhost', 50000), authkey=b'AEPsecret')
    print("Manager server running (data only)...")
    manager.get_server().serve_forever()