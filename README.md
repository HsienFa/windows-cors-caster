# 2RTK NTRIP Caster v2.2.0

**Language / 语言选择:**
- [English](#) (Current)
- [中文版](README-zh.md)

---

This is a simple NTRIP caster written in Python that supports NTRIP v1.0 and v2.0 protocols, managed through a web interface.
You can use the web interface to add users and mount points in your browser, and also view the NTRIP caster connection information.
It supports high concurrent connections and can handle 2000+ concurrent connections. (Due to my limited testing environment, I only tested with 2 RTCM data sources and 2000 concurrent user downloads, but its performance is excellent)

It used to be a single file 2rtk.py,https://github.com/Rampump/2RTKcaster   . but I recently refactored it. Now it looks much fatter, but I still think it's relatively lightweight. lol

- Web-based management. So you can deploy it on any cloud host.
- Uses SQLite database to store user information and mount point information.
- Leverages the pyrtcm library to parse uploaded data and correct STR tables (this part of the code has been rewritten several times, but I'm still not satisfied with it, including the current version. Looking forward to future updates).
    (You just need to add a mount point on the web page and upload RTCM data, 2rtk-NtripCaster will automatically generate STR information and parse RTCM data to extract data types and location information to correct the STR)
- This NTRIP caster is still far from the caster in my mind. I will gradually improve it in my spare time.
- It supports most systems with Python environment, including debian, ubuntu, centos, armbian, etc.
- Supports Docker deployment.

## Installation Tutorials

### Windows 11 Native

- Use 64-bit Python 3.11; Docker, WSL, Linux, and Redis are not required.
- See [Windows 11 native installation](WINDOWS-INSTALL.md).

### Docker Deployment

Create an ignored `.env` securely, review it locally before first start, and validate Compose:

```bash
python3 scripts/deployment_config.py prepare-env --env-file .env --example .env.example
docker compose config --quiet
docker compose up -d ntrip-caster
```

- Web publishing defaults to `127.0.0.1:5757`; NTRIP defaults to externally reachable TCP port 2101.
- Restrict NTRIP source networks with a firewall and never publish `.env` or runtime configuration.
- **中文教程**: [Docker 安裝與使用](DOCKER-TUTORIAL.md)
- **English Tutorial**: [Docker Installation and Usage Guide](DOCKER-TUTORIAL-EN.md)

### Debian System Native Installation
- **中文教程**: [Linux 系统原生安装教程](INSTALL-TUTORIAL.md)
- **English Tutorial**: [Linux Native Installation Guide](INSTALL-TUTORIAL-EN.md)

Native Web management defaults to `http://127.0.0.1:5757`. NTRIP uses TCP 2101 and is exposed only when the
operator intentionally configures host binding, port publishing, and firewall access.
- Administrator username: `admin`; set a private password before starting the service.

## Hardware Recommendations

### Minimum Configuration Requirements
- **CPU**: 2 cores (x86_64 architecture recommended)
- **Memory**: 2GB RAM
- **Storage**: 10GB available disk space
- **Network**: Stable network connection
- **Operating System**: Ubuntu 18.04+ / Debian 10+ / CentOS 7+

## Frontend Web Interface Features
### Homepage
You can see the current caster's running status on the homepage, including connection count, user count, mount point count, etc. The log information below will push user or mount point connection status in real-time. DEBUG mode will push more debugging information.

![Homepage](img/Home.png)

### User Management Page
You can add users, delete users, modify user passwords, etc. on the user management page. You can also see online users. (User management will be added later, API is reserved)

![User Management](img/user.png)

### Mount Point Management Page
You can add mount points, delete mount points, modify mount point information, etc. on the mount point management page. You can also see online information. (Mount point management will be added later, API is reserved)

![Mount Point Management](img/mount.png)

### Base Station Information Page
You can view RTCM status on the base station information page. Click the INFO button in front of the STR entry, and the backend will parse it and display it in the information below. (This usually takes some time to parse before updating the display)

![Base Station Information](img/rtcm.png)

### Configuration Recommendations for Different Loads

| Concurrent Connections | CPU | Memory | Storage | Network Bandwidth |
|------------------------|-----|--------|---------|------------------|
| **< 100** | 1 core | 1GB | 5GB | 10Mbps |
| **100-500** | 2 cores | 2GB | 10GB | 50Mbps |
| **500-1000** | 4 cores | 4GB | 20GB | 100Mbps |
| **1000-2000** | 8 cores | 8GB | 50GB | 200Mbps |
| **2000+** | 16+ cores | 16GB+ | 100GB+ | 500Mbps+ |

### Cloud Server Recommendations
For cloud deployment, expose TCP 2101 only when remote NTRIP clients require it. Keep Web management local
or place it behind a hardened TLS reverse proxy.
#### AWS EC2
- **Entry Level**: t3.small (2 cores 2GB)
- **Standard**: c5.large (2 cores 4GB)
- **High Performance**: c5.2xlarge (8 cores 16GB)

## Performance Benchmark Tests

- **500 Connection Test**: CPU 18.1%, Memory 29.5%, Network 7.47 Mbps
- **1000 Connection Test**: CPU 19.1%, Memory 33.9%, Network 10.79 Mbps
- **2000 Connection Limit Test**: CPU 17.3%, Memory 30.3%, Network 7.69 Mbps

> For detailed test reports, please check the [tests/](tests/) directory

## Configuration Guide

### Main Configuration Options

```ini
[network]
host = 127.0.0.1               # Safe native default
max_connections = 5000

[ntrip]
host = 127.0.0.1
port = 2101                    # NTRIP service port

[web]
host = 127.0.0.1
port = 5757                    # Web management port

[performance]
thread_pool_size = 5000        # Concurrent connection thread pool size
max_workers = 5000             # Maximum worker threads

[data_forwarding]
ring_buffer_size = 60          # Ring buffer size

[security]
secret_key = REPLACE_WITH_RANDOM_SECRET_KEY
```

```ini
[admin]
username = admin               # Administrator username
password = REPLACE_WITH_STRONG_ADMIN_PASSWORD
```

### Common Issue Diagnosis

#### Port Occupation Issues
```bash
# Check port occupation
sudo netstat -tlnp | grep :2101    # NTRIP port
sudo netstat -tlnp | grep :5757    # Web management port
sudo lsof -i :2101                 # Check port usage

# Release port
sudo kill -9 <PID>                 # Force terminate process
sudo fuser -k 2101/tcp             # Force release port
```

#### Network Connection Issues
```bash
# Firewall check
sudo ufw status                    # Ubuntu firewall
sudo firewall-cmd --list-all       # CentOS firewall

# Open only NTRIP when remote clients are required
sudo ufw allow 2101/tcp

# Network connectivity test
telnet localhost 2101              # Test NTRIP port
curl http://localhost:5757/        # Test Web port
```

### Performance Optimization

#### High Concurrency Configuration
```ini
# config.ini optimization configuration
[ntrip]
port = 2101

[network]
max_connections = 10000            # Maximum connections

[performance]
thread_pool_size = 10000           # Thread pool size
max_workers = 10000                # Maximum worker threads

[data_forwarding]
ring_buffer_size = 60              # Ring buffer

[network]
buffer_size = 16384                # Network buffer
timeout = 30                       # Connection timeout
```

#### System-level Optimization
```bash
# Increase file descriptor limit
echo "* soft nofile 65536" >> /etc/security/limits.conf
echo "* hard nofile 65536" >> /etc/security/limits.conf

# Network parameter optimization
echo "net.core.somaxconn = 65536" >> /etc/sysctl.conf
echo "net.ipv4.tcp_max_syn_backlog = 65536" >> /etc/sysctl.conf
sudo sysctl -p
```

### Monitoring and Logging

#### Log Level Configuration
```ini
# config.ini log configuration
[logging]
log_level = INFO                   # DEBUG, INFO, WARNING, ERROR
log_dir = logs
max_log_size = 10485760
backup_count = 10
```
## Contributing

- Welcome to submit Pull Requests
- Contact: i@jia.by
- 2rtk.com


## Acknowledgments and Open Source Libraries

This project uses the following excellent open source libraries and tools, and we express our sincere gratitude:

### Core Dependencies

| Library | Version | Purpose | License |
|---------|---------|---------|----------|
| **Flask** | 2.3.3 | Web framework, providing HTTP services and APIs | BSD-3-Clause |
| **Flask-SocketIO** | 5.3.6 | WebSocket real-time communication support | MIT |
| **python-socketio** | 5.8.0 | Socket.IO protocol implementation | MIT |
| **psutil** | 5.9.5 | System performance monitoring and resource statistics | BSD-3-Clause |
| **pyproj** | 3.6.1 | Geographic coordinate system conversion and projection calculation | MIT |

### RTCM Parsing Library

**pyrtcm** - Core RTCM message parsing library
- **Source**: Integrated based on standard [pyrtcm](https://github.com/semuconsulting/pyrtcm) library source code
- **Version**: Integrated version (to prevent upstream repository deletion risk)
- **Author**: semuconsulting
- **License**: BSD-3-Clause
- **Purpose**: Provides complete RTCM 3.x message parsing, encoding and decoding functions
- **Note**: To ensure project stability, it is recommended to directly integrate the pyrtcm library source code into the project to avoid external dependency risks

## Open Source License

This project is licensed under the [Apache License 2.0](LICENSE).

### Third-party Library Licenses

- **pyrtcm**: BSD-3-Clause License
- **Flask series**: MIT/BSD License
- **psutil**: BSD-3-Clause License
- **pyproj**: MIT License

All integrated third-party libraries maintain their original open source licenses. Please comply with the corresponding license terms when using.

---

** If this project helps you, please give me a Star!**
