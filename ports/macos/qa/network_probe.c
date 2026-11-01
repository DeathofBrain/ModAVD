/* Bounded Android/host loopback QA; no external services or disk payloads. */
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define MAX_SOCKETS 160
#define DEADLINE_MS 15000

static uint64_t now_ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) abort();
    return (uint64_t)t.tv_sec * 1000 + (uint64_t)t.tv_nsec / 1000000;
}

static unsigned char pattern(unsigned index, uint64_t offset) {
    return (unsigned char)(index * 17u + offset * 29u + (offset >> 8));
}

static struct sockaddr_in address(const char *host, unsigned port) {
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons((uint16_t)port)};
    if (inet_pton(AF_INET, host, &a.sin_addr) != 1) abort();
    return a;
}

static int wait_fd(int fd, short events) {
    struct pollfd p = {.fd = fd, .events = events};
    int n;
    do { n = poll(&p, 1, DEADLINE_MS); } while (n < 0 && errno == EINTR);
    return n == 1 ? 0 : -1;
}

static int send_all(int fd, const void *buffer, size_t size) {
    const unsigned char *b = buffer;
    while (size) {
        ssize_t n = send(fd, b, size, 0);
        if (n > 0) { b += n; size -= (size_t)n; }
        else if (n < 0 && errno == EINTR) continue;
        else if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            if (wait_fd(fd, POLLOUT)) return -1;
        } else return -1;
    }
    return 0;
}

/* Every connection is initiated before any response is consumed. The server
 * waits for the entire batch before sending, proving >128 live TCP streams. */
static int tcp_batch(const char *host, unsigned port, const char *token,
                     const char *mode, unsigned count, uint64_t bytes) {
    struct pollfd p[MAX_SOCKETS];
    unsigned char state[MAX_SOCKETS] = {0};
    uint64_t received[MAX_SOCKETS] = {0};
    unsigned established = 0, done = 0, bad = 0;
    uint64_t started = now_ms(), deadline = started + DEADLINE_MS;
    struct sockaddr_in a = address(host, port);
    for (unsigned i = 0; i < count; ++i) p[i].fd = -1;
    for (unsigned i = 0; i < count; ++i) {
        p[i].fd = socket(AF_INET, SOCK_STREAM, 0);
        p[i].events = POLLOUT;
        if (p[i].fd < 0 || fcntl(p[i].fd, F_SETFL, O_NONBLOCK) < 0) { ++bad; goto finish; }
        if (connect(p[i].fd, (struct sockaddr *)&a, sizeof(a)) && errno != EINPROGRESS) {
            ++bad; goto finish;
        }
    }
    while (done < count && now_ms() < deadline) {
        int n = poll(p, count, 100);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) { ++bad; break; }
        for (unsigned i = 0; i < count; ++i) {
            if (!p[i].revents || state[i] == 2) continue;
            if (state[i] == 0) {
                int error = 0;
                socklen_t len = sizeof(error);
                if (getsockopt(p[i].fd, SOL_SOCKET, SO_ERROR, &error, &len) || error) {
                    ++bad; goto finish;
                }
                char request[128];
                int size = snprintf(request, sizeof(request), "MODNET %s %s %u %u %" PRIu64 "\n",
                                    token, mode, i, count, bytes);
                if (size < 0 || (size_t)size >= sizeof(request) || send_all(p[i].fd, request, (size_t)size)) {
                    ++bad; goto finish;
                }
                if (!strcmp(mode, "half") && shutdown(p[i].fd, SHUT_WR)) { ++bad; goto finish; }
                state[i] = 1; p[i].events = POLLIN; ++established;
            } else {
                unsigned char buffer[32768];
                ssize_t size = recv(p[i].fd, buffer, sizeof(buffer), 0);
                if (size > 0) {
                    for (ssize_t j = 0; j < size; ++j) {
                        if (received[i] >= bytes || buffer[j] != pattern(i, received[i])) ++bad;
                        ++received[i];
                    }
                } else if (!size) {
                    if (received[i] != bytes) ++bad;
                    close(p[i].fd); p[i].fd = -1; state[i] = 2; ++done;
                } else if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK) {
                    ++bad; goto finish;
                }
            }
        }
    }
finish:
    if (done != count) ++bad;
    uint64_t total = 0;
    for (unsigned i = 0; i < count; ++i) {
        total += received[i];
        if (p[i].fd >= 0) close(p[i].fd);
    }
    uint64_t elapsed = now_ms() - started;
    printf("{\"case\":\"%s\",\"connections\":%u,\"established\":%u,\"completed\":%u,"
           "\"bytes\":%" PRIu64 ",\"elapsed_ms\":%" PRIu64 ",\"errors\":%u,\"passed\":%s}\n",
           mode, count, established, done, total, elapsed, bad, bad ? "false" : "true");
    fflush(stdout);
    return bad ? 1 : 0;
}

static int refused(const char *host, unsigned port) {
    struct sockaddr_in a = address(host, port);
    int fd = socket(AF_INET, SOCK_STREAM, 0), error = 0;
    socklen_t length = sizeof(error);
    int ok = fd >= 0 && !fcntl(fd, F_SETFL, O_NONBLOCK);
    uint64_t started = now_ms();
    if (ok) {
        int n = connect(fd, (struct sockaddr *)&a, sizeof(a));
        if (n && errno == EINPROGRESS) {
            ok = !wait_fd(fd, POLLOUT) && !getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length);
        } else { error = n ? errno : 0; }
        ok = ok && error == ECONNREFUSED;
    }
    if (fd >= 0) close(fd);
    printf("{\"case\":\"refused\",\"errno\":%d,\"elapsed_ms\":%" PRIu64 ",\"passed\":%s}\n",
           error, now_ms() - started, ok ? "true" : "false");
    return ok ? 0 : 1;
}

static int reset(const char *host, unsigned port, const char *token) {
    struct sockaddr_in a = address(host, port);
    int fd = socket(AF_INET, SOCK_STREAM, 0), error = 0, ok = fd >= 0;
    socklen_t length = sizeof(error);
    unsigned received = 0;
    if (ok) ok = !fcntl(fd, F_SETFL, O_NONBLOCK);
    if (ok) {
        int n = connect(fd, (struct sockaddr *)&a, sizeof(a));
        ok = (!n || errno == EINPROGRESS) && !wait_fd(fd, POLLOUT) &&
             !getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) && !error;
    }
    if (ok) {
        char request[128];
        int n = snprintf(request, sizeof(request), "MODNET %s reset 0 1 0\n", token);
        ok = n > 0 && (size_t)n < sizeof(request) && !send_all(fd, request, (size_t)n);
    }
    uint64_t deadline = now_ms() + DEADLINE_MS;
    while (ok && now_ms() < deadline) {
        if (wait_fd(fd, POLLIN)) { ok = 0; break; }
        unsigned char buffer[1024];
        ssize_t n = recv(fd, buffer, sizeof(buffer), 0);
        if (n > 0) { received += (unsigned)n; if (received > 64) ok = 0; }
        else if (!n) { error = 0; break; }
        else if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) { error = errno; break; }
    }
    ok = ok && error == ECONNRESET;
    if (fd >= 0) close(fd);
    printf("{\"case\":\"reset\",\"errno\":%d,\"received_bytes\":%u,\"passed\":%s}\n",
           error, received, ok ? "true" : "false");
    return ok ? 0 : 1;
}

static int udp(const char *host, unsigned port, const char *token) {
    struct pollfd p[MAX_SOCKETS];
    unsigned done = 0, bad = 0;
    struct sockaddr_in a = address(host, port);
    for (unsigned i = 0; i < MAX_SOCKETS; ++i) p[i].fd = -1;
    for (unsigned i = 0; i < MAX_SOCKETS; ++i) {
        unsigned char packet[296];
        memcpy(packet, token, 32);
        uint32_t index = htonl(i); memcpy(packet + 32, &index, 4);
        memcpy(packet + 36, "UDP1", 4);
        for (unsigned j = 0; j < 256; ++j) packet[40 + j] = pattern(i, j);
        p[i].fd = socket(AF_INET, SOCK_DGRAM, 0); p[i].events = POLLIN;
        if (p[i].fd < 0 || connect(p[i].fd, (struct sockaddr *)&a, sizeof(a)) ||
            send(p[i].fd, packet, sizeof(packet), 0) != sizeof(packet)) { ++bad; goto finish; }
    }
    uint64_t deadline = now_ms() + DEADLINE_MS;
    while (done < MAX_SOCKETS && now_ms() < deadline) {
        int n = poll(p, MAX_SOCKETS, 100);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) { ++bad; break; }
        for (unsigned i = 0; i < MAX_SOCKETS; ++i) {
            if (p[i].fd < 0 || !p[i].revents) continue;
            unsigned char packet[297];
            ssize_t length = recv(p[i].fd, packet, sizeof(packet), 0);
            uint32_t index = 0;
            if (length >= 36) memcpy(&index, packet + 32, 4);
            if (length != 296 || memcmp(packet, token, 32) || ntohl(index) != i ||
                memcmp(packet + 36, "UDP1", 4)) ++bad;
            else for (unsigned j = 0; j < 256; ++j) if (packet[40 + j] != pattern(i, j)) ++bad;
            close(p[i].fd); p[i].fd = -1; ++done;
        }
    }
finish:
    if (done != MAX_SOCKETS) ++bad;
    for (unsigned i = 0; i < MAX_SOCKETS; ++i) if (p[i].fd >= 0) close(p[i].fd);
    printf("{\"case\":\"udp\",\"sockets\":%u,\"completed\":%u,\"payload_bytes_each\":256,"
           "\"errors\":%u,\"passed\":%s}\n", MAX_SOCKETS, done, bad, bad ? "false" : "true");
    return bad ? 1 : 0;
}

int main(int argc, char **argv) {
    if (argc != 6 || strlen(argv[5]) != 32 || strspn(argv[5], "0123456789abcdef") != 32) return 2;
    unsigned ports[3];
    for (unsigned i = 0; i < 3; ++i) {
        char *end;
        unsigned long n = strtoul(argv[i + 2], &end, 10);
        if (*end || n < 1024 || n > 65535) return 2;
        ports[i] = (unsigned)n;
    }
    struct in_addr parsed;
    if (inet_pton(AF_INET, argv[1], &parsed) != 1) return 2;
    signal(SIGPIPE, SIG_IGN);
    int failed = 0;
    failed += tcp_batch(argv[1], ports[0], argv[5], "many", 160, 8192);
    failed += tcp_batch(argv[1], ports[0], argv[5], "single", 1, 32u * 1024 * 1024);
    failed += tcp_batch(argv[1], ports[0], argv[5], "parallel", 8, 4u * 1024 * 1024);
    failed += tcp_batch(argv[1], ports[0], argv[5], "half", 1, 4096);
    failed += refused(argv[1], ports[2]);
    failed += reset(argv[1], ports[0], argv[5]);
    failed += udp(argv[1], ports[1], argv[5]);
    /* A reset/refusal/UDP batch must not prevent a fresh successful TCP stream. */
    failed += tcp_batch(argv[1], ports[0], argv[5], "recovery", 1, 8192);
    return failed ? 1 : 0;
}
