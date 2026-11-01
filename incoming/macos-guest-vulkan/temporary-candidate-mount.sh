#!/system/bin/sh
# QA only: reversible bind mount for a fresh standalone native GPU probe.
# Run as root in the same mount namespace as the probe. Never restarts zygote.
# This script is not part of the Manager guest startup implementation.
set -eu
v=/vendor/lib64/libvulkan_enc.so
p=/data/local/tmp/modavd-vulkan-arm64-candidate-v2.so
orig=d6188011ed2ed58a1d69adfb04246abaa2595c0f15565a22da83f7751a3d6828
fix=73ddfe0935826feef1f1a78d5a70af3ee7fa83584d2efaf5bad6ee91f36eb613
sha() { sha256sum "$1" | cut -d' ' -f1; }
mounted() { grep -Fq " $v " /proc/self/mountinfo; }
die() { echo "FAILED $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "requires root"
echo "MOUNT_NAMESPACE $(readlink /proc/self/ns/mnt)"
case ${1:-} in
install)
    mounted && die "existing mount over $v; unchanged"
    [ "$(sha "$v")" = "$orig" ] || die "unexpected original driver"
    [ -f "$p" ] && [ "$(sha "$p")" = "$fix" ] || die "candidate absent or hash mismatch"
    label=$(ls -Z "$v" | cut -d' ' -f1)
    chmod 644 "$p"
    chcon "$label" "$p"
    [ "$(ls -Z "$p" | cut -d' ' -f1)" = "$label" ] || die "candidate label mismatch"
    mount --bind "$p" "$v"
    if ! mounted || [ "$(sha "$v")" != "$fix" ]; then
        umount "$v" || true
        die "mount verification failed; rollback attempted"
    fi
    echo "MOUNTED $fix"
    ;;
rollback)
    mounted || die "no driver mount to roll back"
    [ "$(sha "$v")" = "$fix" ] || die "mounted driver is not this candidate; unchanged"
    umount "$v"
    ! mounted || die "driver still has a mount after rollback"
    [ "$(sha "$v")" = "$orig" ] || die "original driver hash not restored"
    echo "RESTORED $orig"
    ;;
*) die "usage: $0 install|rollback" ;;
esac
