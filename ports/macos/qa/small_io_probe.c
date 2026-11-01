/* Bounded Android 8 KiB + fsync measurement, with byte-for-byte readback. */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static uint64_t ns(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value)) abort();
    return (uint64_t)value.tv_sec * 1000000000 + value.tv_nsec;
}

static void pattern(unsigned char *buffer, unsigned number) {
    uint32_t x = number + 0x9e3779b9;
    for (size_t i = 0; i < 8192; i++) {
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        buffer[i] = (unsigned char)x;
    }
}

static int compare(const void *a, const void *b) {
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

int main(int argc, char **argv) {
    const char prefix[] = "/data/local/tmp/modavd-gapfix-";
    if (argc != 3 || strncmp(argv[1], prefix, sizeof(prefix) - 1)) return 2;
    char *end;
    unsigned long count = strtoul(argv[2], &end, 10);
    if (*end || count < 128 || count > 4096) return 2;
    int fd = open(argv[1], O_CREAT | O_EXCL | O_NOFOLLOW | O_RDWR | O_CLOEXEC, 0600);
    if (fd < 0) { perror("open"); return 3; }
    int status = 1;
    uint64_t times[4096], start = ns(), write_end;
    unsigned char buffer[8192], actual[8192];
    for (unsigned long number = 0; number < count; number++) {
        pattern(buffer, (unsigned)number);
        uint64_t before = ns();
        size_t done = 0;
        while (done < sizeof(buffer)) {
            ssize_t wrote = write(fd, buffer + done, sizeof(buffer) - done);
            if (wrote < 0 && errno == EINTR) continue;
            if (wrote <= 0) goto cleanup;
            done += (size_t)wrote;
        }
        if (fsync(fd)) goto cleanup;
        times[number] = ns() - before;
    }
    write_end = ns();
    if (lseek(fd, 0, SEEK_SET) < 0) goto cleanup;
    for (unsigned long number = 0; number < count; number++) {
        pattern(buffer, (unsigned)number);
        size_t done = 0;
        while (done < sizeof(actual)) {
            ssize_t got = read(fd, actual + done, sizeof(actual) - done);
            if (got < 0 && errno == EINTR) continue;
            if (got <= 0) goto cleanup;
            done += (size_t)got;
        }
        if (memcmp(buffer, actual, sizeof(buffer))) goto cleanup;
    }
    qsort(times, count, sizeof(times[0]), compare);
    printf("{\"bytes\":%lu,\"operations\":%lu,\"write_fsync_ns\":%llu,\"p50_ns\":%llu,\"p95_ns\":%llu,\"readback\":true}\n",
           count * 8192, count, (unsigned long long)(write_end - start),
           (unsigned long long)times[count / 2], (unsigned long long)times[count * 95 / 100]);
    status = 0;
cleanup:
    if (status) perror("I/O or readback failed");
    if (close(fd) || unlink(argv[1])) return 4;
    return status;
}
