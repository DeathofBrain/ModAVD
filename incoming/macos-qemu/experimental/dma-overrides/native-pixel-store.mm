#import <Cocoa/Cocoa.h>
#include <OpenGL/gl3.h>
#include "modavd-dma-pixel-store.h"

#include <array>
#include <cstdint>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {
struct NativeDispatch {
    decltype(&::glGetIntegerv) glGetIntegerv = &::glGetIntegerv;
    decltype(&::glBindBuffer) glBindBuffer = &::glBindBuffer;
    decltype(&::glPixelStorei) glPixelStorei = &::glPixelStorei;
};

using State = std::array<GLint, 10>;
constexpr std::array<GLenum, 10> kStateNames = {
    GL_PACK_ALIGNMENT, GL_PACK_ROW_LENGTH, GL_PACK_SKIP_ROWS, GL_PACK_SKIP_PIXELS,
    GL_PIXEL_PACK_BUFFER_BINDING,
    GL_UNPACK_ALIGNMENT, GL_UNPACK_ROW_LENGTH, GL_UNPACK_SKIP_ROWS, GL_UNPACK_SKIP_PIXELS,
    GL_PIXEL_UNPACK_BUFFER_BINDING,
};

State state() {
    State result{};
    for (size_t i = 0; i < result.size(); ++i) glGetIntegerv(kStateNames[i], &result[i]);
    return result;
}

bool normalized(const State& current, const State& saved, bool pack) {
    State expected = saved;
    const size_t start = pack ? 0 : 5;
    for (size_t i = 0; i < 5; ++i) expected[start + i] = i == 0 ? 1 : 0;
    return current == expected;
}

std::string quote(const std::string& value) {
    std::string result = "\"";
    for (unsigned char c : value) {
        if (c == '\\' || c == '"') { result += '\\'; result += static_cast<char>(c); }
        else if (c == '\n') result += "\\n";
        else if (c >= 32 && c < 127) result += static_cast<char>(c);
        else result += '?';
    }
    return result + '"';
}

struct Evidence {
    std::string renderer, version, vendor, shading;
    std::string failure;
    GLint major = 0, minor = 0, profile = 0;
    GLenum unpackNegative = 0, packNegative = 0;
    std::vector<std::pair<std::string, bool>> checks;
    std::vector<std::pair<std::string, GLenum>> errors;
    State poisoned{}, restored{};

    void check(std::string name, bool value) { checks.emplace_back(std::move(name), value); }
    void error(const std::string& stage) {
        for (int n = 0; n < 32; ++n) {
            GLenum value = glGetError();
            if (value == GL_NO_ERROR) return;
            errors.emplace_back(stage, value);
        }
    }
    int emit() const {
        bool pass = failure.empty() && errors.empty() && !checks.empty();
        for (const auto& entry : checks) pass = pass && entry.second;
        std::cout << "{\n  \"schema\": 1,\n  \"pass\": " << (pass ? "true" : "false")
                  << ",\n  \"context\": {\"api\": \"NSOpenGL/CGL\", \"requested_core\": \"3.2\", "
                  << "\"accelerated_requested\": true, \"major\": " << major << ", \"minor\": " << minor
                  << ", \"profile_mask\": " << profile << ", \"renderer\": " << quote(renderer)
                  << ", \"vendor\": " << quote(vendor) << ", \"version\": " << quote(version)
                  << ", \"shading_language\": " << quote(shading) << "},\n"
                  << "  \"transfer\": {\"width\": 4, \"height\": 4, \"format\": \"RGBA8\", "
                  << "\"client_payload_bytes\": 64, \"client_allocation_bytes\": 8192, \"client_payload_offset\": 1024, "
                  << "\"pbo_allocation_bytes\": 8192},\n"
                  << "  \"negative_control\": {\"unscoped_unpack_gl_error\": " << unpackNegative
                  << ", \"unscoped_pack_gl_error\": " << packNegative << "},\n  \"checks\": {";
        for (size_t i = 0; i < checks.size(); ++i) {
            std::cout << (i ? "," : "") << "\n    " << quote(checks[i].first) << ": "
                      << (checks[i].second ? "true" : "false");
        }
        std::cout << "\n  },\n  \"poisoned_state\": [";
        for (size_t i = 0; i < poisoned.size(); ++i) std::cout << (i ? ", " : "") << poisoned[i];
        std::cout << "],\n  \"restored_state\": [";
        for (size_t i = 0; i < restored.size(); ++i) std::cout << (i ? ", " : "") << restored[i];
        std::cout << "],\n  \"unexpected_gl_errors\": [";
        for (size_t i = 0; i < errors.size(); ++i) {
            std::cout << (i ? ", " : "") << "{\"stage\":" << quote(errors[i].first)
                      << ",\"code\":" << errors[i].second << "}";
        }
        std::cout << "],\n  \"failure\": " << quote(failure) << "\n}\n";
        return pass ? 0 : 1;
    }
};

std::string glString(GLenum name) {
    const auto* value = glGetString(name);
    return value ? reinterpret_cast<const char*>(value) : "";
}
}  // namespace

int main() {
    @autoreleasepool {
        Evidence evidence;
        NSOpenGLPixelFormatAttribute attributes[] = {
            NSOpenGLPFAOpenGLProfile, NSOpenGLProfileVersion3_2Core,
            NSOpenGLPFAAccelerated, NSOpenGLPFANoRecovery,
            NSOpenGLPFAColorSize, 24, NSOpenGLPFAAlphaSize, 8, 0,
        };
        NSOpenGLPixelFormat* format = [[NSOpenGLPixelFormat alloc] initWithAttributes:attributes];
        if (!format) {
            evidence.failure = "NSOpenGLPixelFormat rejected accelerated 3.2 core attributes";
            return evidence.emit();
        }
        NSOpenGLContext* context = [[NSOpenGLContext alloc] initWithFormat:format shareContext:nil];
        if (!context) {
            evidence.failure = "NSOpenGLContext creation failed";
            return evidence.emit();
        }
        [context makeCurrentContext];
        evidence.check("real_cgl_context_current", CGLGetCurrentContext() == [context CGLContextObj]);
        evidence.renderer = glString(GL_RENDERER);
        evidence.vendor = glString(GL_VENDOR);
        evidence.version = glString(GL_VERSION);
        evidence.shading = glString(GL_SHADING_LANGUAGE_VERSION);
        glGetIntegerv(GL_MAJOR_VERSION, &evidence.major);
        glGetIntegerv(GL_MINOR_VERSION, &evidence.minor);
        glGetIntegerv(GL_CONTEXT_PROFILE_MASK, &evidence.profile);
        evidence.check("core_context_at_least_3_2", (evidence.major > 3 ||
            (evidence.major == 3 && evidence.minor >= 2)) &&
            (evidence.profile & GL_CONTEXT_CORE_PROFILE_BIT) != 0);
        evidence.error("context-info");

        GLuint texture = 0, framebuffer = 0;
        glGenTextures(1, &texture);
        glBindTexture(GL_TEXTURE_2D, texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 4, 4, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
        glGenFramebuffers(1, &framebuffer);
        glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
        glReadBuffer(GL_COLOR_ATTACHMENT0);
        glDrawBuffer(GL_COLOR_ATTACHMENT0);
        evidence.check("rgba8_fbo_complete", glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE);
        evidence.error("texture-fbo-create");

        constexpr size_t allocation = 8192, offset = 1024, payload = 64;
        std::vector<uint8_t> input(allocation, 0xa5), output(allocation, 0x5a);
        for (size_t y = 0; y < 4; ++y) for (size_t x = 0; x < 4; ++x) {
            const size_t at = offset + (y * 4 + x) * 4;
            input[at] = 11 + 17 * x + 3 * y;
            input[at + 1] = 19 + 9 * x + 31 * y;
            input[at + 2] = 23 + 13 * x + 27 * y;
            input[at + 3] = 255;
        }
        const auto originalInput = input;
        std::vector<uint8_t> packInitial(allocation, 0xc6), unpackInitial(allocation, 0x6c);
        GLuint buffers[2]{};
        glGenBuffers(2, buffers);
        glBindBuffer(GL_PIXEL_PACK_BUFFER, buffers[0]);
        glBufferData(GL_PIXEL_PACK_BUFFER, allocation, packInitial.data(), GL_STATIC_DRAW);
        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, buffers[1]);
        glBufferData(GL_PIXEL_UNPACK_BUFFER, allocation, unpackInitial.data(), GL_STATIC_DRAW);
        const std::array<GLint, 4> packPoison{8, 17, 3, 2}, unpackPoison{8, 19, 2, 3};
        for (size_t i = 0; i < 4; ++i) {
            glPixelStorei(kStateNames[i], packPoison[i]);
            glPixelStorei(kStateNames[5 + i], unpackPoison[i]);
        }
        evidence.poisoned = state();
        evidence.check("nonzero_distinct_pixel_buffers", buffers[0] && buffers[1] && buffers[0] != buffers[1]);
        evidence.check("pack_unpack_poison_applied", evidence.poisoned == State{
            8, 17, 3, 2, static_cast<GLint>(buffers[0]),
            8, 19, 2, 3, static_cast<GLint>(buffers[1])});
        evidence.error("poison-state");

        // Without the scope these host addresses are interpreted as huge PBO
        // offsets and rejected by real GL, proving the poisoned state matters.
        evidence.check("tls_initially_inactive", !modavd_dma_precise_pixel_transfer_active());
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 4, 4, GL_RGBA, GL_UNSIGNED_BYTE, input.data() + offset);
        evidence.unpackNegative = glGetError();
        glReadPixels(0, 0, 4, 4, GL_RGBA, GL_UNSIGNED_BYTE, output.data() + offset);
        evidence.packNegative = glGetError();
        evidence.check("unscoped_poison_rejected_by_gl", evidence.unpackNegative == GL_INVALID_OPERATION &&
            evidence.packNegative == GL_INVALID_OPERATION);
        evidence.error("negative-control-tail");

        NativeDispatch dispatch;
        evidence.check("enable_returns_previous_false", !modavd_dma_set_precise_pixel_transfer(true));
        {
            ModavdDmaPixelStoreScope<NativeDispatch> scope(dispatch, false);
            evidence.check("unpack_scope_reports_ok", scope.isOk());
            evidence.check("unpack_scope_only_normalizes_unpack", normalized(state(), evidence.poisoned, false));
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 4, 4, GL_RGBA, GL_UNSIGNED_BYTE, input.data() + offset);
            evidence.error("scoped-real-upload");
        }
        evidence.check("unpack_scope_restores_all_ten_states", state() == evidence.poisoned);
        {
            ModavdDmaPixelStoreScope<NativeDispatch> scope(dispatch, true);
            evidence.check("pack_scope_reports_ok", scope.isOk());
            evidence.check("pack_scope_only_normalizes_pack", normalized(state(), evidence.poisoned, true));
            glReadPixels(0, 0, 4, 4, GL_RGBA, GL_UNSIGNED_BYTE, output.data() + offset);
            evidence.error("scoped-real-readback");
        }
        evidence.restored = state();
        evidence.check("pack_scope_restores_all_ten_states", evidence.restored == evidence.poisoned);
        evidence.check("tls_remains_active_after_gl_scopes", modavd_dma_precise_pixel_transfer_active());
        evidence.check("disable_returns_previous_true", modavd_dma_set_precise_pixel_transfer(false));
        evidence.check("tls_inactive_after_restore", !modavd_dma_precise_pixel_transfer_active());

        for (bool pack : {false, true}) {
            {
                ModavdDmaPixelStoreScope<NativeDispatch> scope(dispatch, pack);
                evidence.check(pack ? "inactive_pack_inside_unchanged" : "inactive_unpack_inside_unchanged",
                    scope.isOk() && state() == evidence.poisoned);
            }
            evidence.check(pack ? "inactive_pack_exit_unchanged" : "inactive_unpack_exit_unchanged",
                state() == evidence.poisoned);
        }
        glFinish();
        evidence.error("finish-and-scope-restoration");

        bool pixelsEqual = true, canariesEqual = true;
        for (size_t i = 0; i < allocation; ++i) {
            if (i >= offset && i < offset + payload) pixelsEqual = pixelsEqual && output[i] == input[i];
            else canariesEqual = canariesEqual && output[i] == 0x5a;
        }
        evidence.check("actual_rgba8_4x4_readback_matches", pixelsEqual);
        evidence.check("client_readback_guard_canaries_intact", canariesEqual);
        evidence.check("client_upload_payload_and_canaries_unchanged", input == originalInput);
        std::vector<uint8_t> actualPack(allocation), actualUnpack(allocation);
        glBindBuffer(GL_COPY_READ_BUFFER, buffers[0]);
        glGetBufferSubData(GL_COPY_READ_BUFFER, 0, allocation, actualPack.data());
        glBindBuffer(GL_COPY_READ_BUFFER, buffers[1]);
        glGetBufferSubData(GL_COPY_READ_BUFFER, 0, allocation, actualUnpack.data());
        evidence.check("pack_pbo_all_canaries_unchanged", actualPack == packInitial);
        evidence.check("unpack_pbo_all_canaries_unchanged", actualUnpack == unpackInitial);
        evidence.check("pbo_inspection_preserves_pixel_store", state() == evidence.poisoned);
        evidence.error("pbo-canary-inspection");

        glBindBuffer(GL_COPY_READ_BUFFER, 0);
        glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
        glDeleteBuffers(2, buffers);
        glDeleteFramebuffers(1, &framebuffer);
        glDeleteTextures(1, &texture);
        evidence.error("cleanup");
        [NSOpenGLContext clearCurrentContext];
        return evidence.emit();
    }
}
