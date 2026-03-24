# NINJAM Server — Video Support Technical Documentation

## Overview

The NINJAM server supports video channels alongside audio, using the same interval-based upload/download protocol. Video data is identified by its **fourcc** codec identifier and routed through the same subscription system as audio. No separate signaling channel is needed — video is just another channel type.

Supported video codecs (fourcc values):
- `H264` — H.264/AVC
- `VP8 ` — VP8 (WebM)
- `MJPG` — Motion JPEG

## Architecture

### Data Flow

```
Client A (sender)                Server                    Client B (subscriber)
    |                              |                              |
    |-- UPLOAD_INTERVAL_BEGIN ---->|                              |
    |   (fourcc=H264, estsize, guid)                             |
    |                              |-- DOWNLOAD_INTERVAL_BEGIN -->|
    |                              |   (username, chidx, guid)   |
    |                              |                              |
    |-- UPLOAD_INTERVAL_WRITE --->|                              |
    |   (guid, audio_data, flags) |-- DOWNLOAD_INTERVAL_WRITE ->|
    |   ...                       |   ...                        |
    |-- UPLOAD_INTERVAL_WRITE --->|                              |
    |   (guid, data, flags=END)   |-- DOWNLOAD_INTERVAL_WRITE ->|
    |                              |   (guid, data, flags=END)   |
```

Video uses the exact same message types as audio (`MESSAGE_CLIENT_UPLOAD_INTERVAL_BEGIN`, `MESSAGE_CLIENT_UPLOAD_INTERVAL_WRITE`). The server distinguishes video from audio solely by the fourcc field in the transfer state.

### Subscription Model

Video channels follow the same subscription model as audio:

1. Client A advertises a video channel via `MESSAGE_CLIENT_SET_CHANNEL_INFO` (with a video-flagged channel)
2. Client B subscribes via `MESSAGE_CLIENT_SET_USERMASK` with the channel bit set
3. Server only relays video data to subscribed clients

### Transfer State Tracking

Each active upload/download is tracked by a `User_TransferState` object:

```cpp
class User_TransferState {
    unsigned char guid[16];      // Unique transfer identifier
    unsigned int fourcc;         // Codec: 'OGGv' for audio, 'H264'/'VP8 '/'MJPG' for video
    unsigned int bytes_estimated;
    unsigned int bytes_sofar;
    time_t last_acttime;         // For timeout tracking
    FILE *fp;                    // Session archive file
};
```

- **Sender side**: `User_Connection::m_recvfiles` — tracks incoming uploads from client
- **Subscriber side**: `User_Connection::m_sendfiles` — tracks outgoing relays to each subscriber

## Thread-Per-Group Architecture

### Problem

A single-threaded server cannot handle HD video relay (720p @ 300kbps+) alongside audio for 4-6 users per room. Video relay blocks audio processing, causing audible glitches.

### Solution

Each private room runs in its own `pthread`. The lobby stays on the main thread. Users are handed off from lobby to room via a thread-safe migration queue.

```
Main thread:     accept connections --> lobby auth --> migration handoff
                                                           |
                 +-----------------------------------------+
                 v                    v                    v
         Room "jazz" thread   Room "rock" thread   Room "blues" thread
         User_Group::ThreadRun (independent)        (independent)
         +- Pass 1: audio msgs
         +- Pass 2: video msgs (drop if congested)
```

### Thread Safety

| Shared Resource          | Protection              | Who Accesses               |
|-------------------------|-------------------------|----------------------------|
| `g_private_groups` map  | `g_groups_mutex`        | Main thread (mutations), stats (read) |
| `g_logfp`               | `g_log_mutex`           | All threads (write)        |
| `m_pending_migrations`  | Per-group `m_migration_mutex` | Main thread (push), group thread (pop) |
| `m_users` list          | **No mutex** — single writer | Group thread only          |
| `g_config_*`            | Read-only after startup | All threads (read)         |
| Lobby `m_group`         | **No mutex** — single writer | Main thread only           |

### Migration Handoff

When a lobby user sends `/join roomname`:

1. **Main thread** removes user from `m_group->m_users`
2. **Main thread** broadcasts `PART` to lobby
3. **Main thread** calls `ng->QueueMigration(c)` — pushes user under `m_migration_mutex`
4. **Room thread** calls `ProcessPendingMigrations()` in its loop
5. **Room thread** adds user to `m_users`, broadcasts `JOIN`, sends userlist + config

### Backward Compatibility

When `PrivateGroupMode` is not configured, no threads are created. The server behaves identically to the original single-threaded implementation.

## Audio Priority (Two-Pass Processing)

When a group runs in its own thread, `User_Group::Run()` uses two-pass processing:

### Pass 1 — Audio Only

Each `User_Connection::Run()` is called with `audio_only=true`. If an incoming message is a video upload (`UPLOAD_INTERVAL_BEGIN` or `UPLOAD_INTERVAL_WRITE` with a video fourcc), the message is stashed in `m_deferred_video_msg` and processing returns immediately. All audio messages are processed normally.

### Pass 2 — Deferred Video

After all users complete Pass 1, the group iterates over users with `m_deferred_video_msg != NULL` and processes those video messages. This ensures audio relay always runs before video within each loop iteration.

### Why This Matters

On a loaded server, a single loop iteration might need to relay:
- 4 audio streams (small OGG packets, ~2-5KB each)
- 4 video streams (H.264 frames, ~20-50KB each)

By processing all audio first, audio latency stays consistent even when video processing is slow.

## Video Frame Dropping (Congestion Control)

### Problem

If a subscriber's TCP connection is slower than the combined video bitrate, the server's per-connection send queue fills up. Without intervention, the queue hits `NET_CON_MAX_MESSAGES` (2048) and audio messages get dropped alongside video.

### Solution

Before sending a video frame to a subscriber, the server checks the subscriber's send queue depth:

```cpp
if (is_video_fourcc(t->fourcc) &&
    u->m_netcon.GetSendQueueCount() > NET_CON_MAX_MESSAGES * g_config_video_congestion_pct / 100)
{
    // Drop this video frame for this subscriber
    // Audio continues flowing normally
}
```

This check happens at two points:

1. **`UPLOAD_INTERVAL_BEGIN`** — Skip creating `User_TransferState` for congested subscribers (they won't receive any frames for this interval)
2. **`UPLOAD_INTERVAL_WRITE`** — Skip forwarding individual video data chunks to congested subscribers

**Key behavior**:
- Only video is dropped — audio is never affected by this check
- Each subscriber is evaluated independently — a slow client only loses its own video
- The sender and other subscribers are unaffected
- When the congested subscriber's queue drains, video delivery resumes automatically

## Configuration Reference

All video-related configuration options in the server `.cfg` file:

### `AllowVideoChannels`

Enable or disable video channel support entirely.

```
AllowVideoChannels yes    # yes | no (default: no)
```

When disabled, clients can still send video data but it will be treated as audio (with the standard 8-second timeout, which will likely cause transfers to fail).

### `VideoTransferTimeout`

Seconds of inactivity before a stale video transfer is cleaned up. Video intervals are longer than audio, so this needs to be higher than the audio timeout (8s).

```
VideoTransferTimeout 30   # 5-300 seconds (default: 30)
```

**When to increase**: If you use very long BPI values (e.g., 64 beats at 60 BPM = 64 seconds per interval) and video transfers are timing out before completion.

**When to decrease**: If you want faster cleanup of abandoned transfers to free memory.

### `VideoCongestionThreshold`

Percentage of the per-connection send queue (`NET_CON_MAX_MESSAGES` = 2048 messages) that triggers video frame dropping for a subscriber.

```
VideoCongestionThreshold 50   # 10-95 percent (default: 50)
```

At default (50%), video frames are dropped when a subscriber has more than 1024 messages queued.

**Lower values** (e.g., 30): More aggressive — drops video earlier to protect audio. Good for low-bandwidth environments.

**Higher values** (e.g., 80): Less aggressive — keeps video flowing longer but risks audio quality if the queue approaches the hard limit.

### `SendBufferKB`

Per-connection TCP send buffer size in kilobytes. Controls how much outbound data the OS can buffer before backpressure reaches the server.

```
SendBufferKB 256   # 64-4096 KB (default: 256)
```

**When to increase**: If you have high-bandwidth clients and want to absorb TCP bursts without triggering congestion-based video drops.

**When to decrease**: If you're running on a memory-constrained server. Each connection allocates this buffer, so `256KB * 50 users = 12.5MB` of OS buffer memory.

### `RecvBufferKB`

Per-connection TCP receive buffer size in kilobytes.

```
RecvBufferKB 128   # 32-2048 KB (default: 128)
```

**When to increase**: If clients are uploading HD video and you see upload stalls.

## Sizing Guide

### Bandwidth Estimation

Per video stream at common quality levels:

| Quality   | Resolution | Bitrate   | Per interval (16 BPI @ 120 BPM) | WRITE msgs/interval |
|-----------|-----------|-----------|----------------------------------|---------------------|
| Low       | 320x240   | ~100 kbps | ~100 KB                         | ~7                  |
| Medium    | 640x480   | ~300 kbps | ~300 KB                         | ~19                 |
| HD        | 1280x720  | ~800 kbps | ~800 KB                         | ~50                 |

Interval duration = `60 * BPI / BPM` seconds. At 16 BPI / 120 BPM = 8 seconds.

### Send Queue Capacity

`NET_CON_MAX_MESSAGES` = 2048 messages. Each WRITE message is up to 16KB (`NET_MESSAGE_MAX_SIZE`).

With 4 users each sending 1 audio + 1 video stream:
- Audio: 4 streams * ~3 msgs/interval = 12 messages
- Video (medium): 4 streams * ~19 msgs/interval = 76 messages
- **Total per interval**: ~88 messages — well within 2048

With 6 users each sending HD video:
- Video: 6 streams * ~50 msgs/interval = 300 messages
- A subscriber receiving all 5 other streams: 250 msgs/interval
- At 50% threshold: drops start at 1024 queued — still comfortable

### Memory Per Connection

```
Send buffer:  256 KB (configurable via SendBufferKB)
Recv buffer:  128 KB (configurable via RecvBufferKB)
Send queue:   2048 * 8 bytes (pointers) = 16 KB
Total:        ~400 KB per connection
```

With 50 concurrent connections: ~20 MB total buffer memory.

### Recommended Configs

**Audio-only server** (default, no video):
```
AllowVideoChannels no
```

**Small jam room with video** (2-4 users, 480p):
```
AllowVideoChannels yes
VideoTransferTimeout 30
VideoCongestionThreshold 50
SendBufferKB 256
RecvBufferKB 128
```

**Larger room with HD video** (4-6 users, 720p):
```
AllowVideoChannels yes
VideoTransferTimeout 60
VideoCongestionThreshold 40
SendBufferKB 512
RecvBufferKB 256
PrivateGroupMode 20
```

**Low-bandwidth environment** (mobile clients, unreliable connections):
```
AllowVideoChannels yes
VideoTransferTimeout 30
VideoCongestionThreshold 30
SendBufferKB 128
RecvBufferKB 64
```

## Troubleshooting

### Video not appearing for subscribers

1. Check `AllowVideoChannels yes` is set in config
2. Verify the subscriber has set the channel subscription mask for the video channel
3. Check server logs for "Error sending message" — indicates send queue full
4. Increase `SendBufferKB` if subscribers have high latency

### Video stutters / drops frames

1. Check if `VideoCongestionThreshold` is being hit — add logging or watch for missing frames client-side
2. Increase `VideoCongestionThreshold` to allow more queue buildup before dropping
3. Reduce video bitrate on the sender side (client setting, not server)
4. Ensure `PrivateGroupMode` is enabled so rooms get dedicated threads

### Audio glitches when video is active

1. Ensure `PrivateGroupMode` is configured — without it, all rooms share the main thread
2. The two-pass processing (audio before video) only activates for threaded rooms
3. If audio still glitches, reduce `VideoCongestionThreshold` to drop video more aggressively
4. Check CPU usage — each room thread needs CPU time

### Transfer timeout errors in logs

1. Increase `VideoTransferTimeout` if intervals are long (high BPI / low BPM)
2. Formula: timeout should be > `60 * BPI / BPM` (interval length in seconds)
3. Default 30s is safe for up to 16 BPI at 40 BPM (24s intervals)

## File Reference

| File | Video-Related Code |
|------|-------------------|
| `ninjam/server/usercon.h` | `User_TransferState::fourcc`, `User_Connection::m_deferred_video_msg`, `User_Group` threading members |
| `ninjam/server/usercon.cpp` | `is_video_fourcc()`, two-pass processing, video frame dropping, congestion check, threading methods |
| `ninjam/server/ninjamsrv.cpp` | `g_config_allow_video_channels`, `g_config_video_transfer_timeout`, `g_config_video_congestion_pct`, `g_config_send_buffer_kb`, `g_config_recv_buffer_kb`, thread creation/join, global mutexes |
| `ninjam/netmsg.h` | `NET_CON_MAX_MESSAGES` (2048), `Net_Connection::GetSendQueueCount()` |
| `ninjam/server/Makefile` | `-lpthread` linking |
| `configs/default.cfg` | Runtime configuration |
| `ninjam/server/example.cfg` | Documented config template |

## Protocol Compatibility

Video support is backward-compatible with standard NINJAM clients:

- **Old clients** ignore video channels (they don't subscribe to unknown fourcc types)
- **Old servers** treat video as audio with an unrecognized fourcc (8s timeout will likely fail)
- No protocol version bump is needed — video uses existing message types
- The fourcc field in `UPLOAD_INTERVAL_BEGIN` has always existed; video just uses new values for it
