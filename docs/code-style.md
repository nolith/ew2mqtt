# Code Style

## Imports

Order: standard library, third-party packages, local modules.

```python
# Standard library
import asyncio
import os
import sys

# Third-party
from bleak import BleakClient
import aiomqtt

# Local modules
import commands
import blemap
from settings import Settings
```

## Naming Conventions

| Type            | Convention             | Example                          |
|-----------------|------------------------|----------------------------------|
| Functions       | `snake_case`           | `set_user_limit()`, `on_exit()`  |
| Variables       | `snake_case`           | `mqtt_host`, `ble_command`       |
| Classes         | `PascalCase`           | `EasyWallbox`, `Settings`        |
| Constants       | `SCREAMING_SNAKE_CASE` | `TOPIC`, `EVCC_STATUS_SOURCE`    |
| Private members | `_leading_underscore`  | `_client`, `_lock`, `_state`     |
| Modules         | `snake_case`           | `blemap.py`, `mqttmap.py`        |

## Async Patterns

This codebase uses `asyncio` extensively:

```python
# Use async/await for all I/O operations
async def connect(self):
    await self._client.connect()

# Use asyncio.Lock for thread-safe state management
async with self._lock:
    self._state = new_state

# Use asyncio.Queue for task coordination
await self._queue.put(command)

# Create background tasks with asyncio.create_task
self.task = asyncio.create_task(self._run())
```

## Error Handling

Log errors with context; avoid silently swallowing exceptions:

```python
try:
    await self._client.connect()
except Exception as e:
    log.error(f"Failed to connect: {e}")
    # Handle gracefully or re-raise
```

## Logging

Use the `logging` module. Both %-style and f-strings are acceptable:

```python
import logging

log = logging.getLogger()
log.setLevel(logging.INFO)

log.info("Message with %s substitution", variable)
log.error(f"Error message: {error}")
log.warn("Warning about %s", condition)
```

## Type Hints

Not currently used, but encouraged for new code:

```python
async def set_user_limit(limit: int, millis: bool = False) -> str:
    ...
```
