#import <Cocoa/Cocoa.h>
#import <OpenGL/OpenGL.h>
#include <stdio.h>
#include "mac_native.h"

// Keep real NSOpenGL/CGL objects and thread state, but deterministically simulate
// a void native bind that silently leaves the previous context current.
@interface RefusingContext : NSOpenGLContext
- (void)preBind:(int)forPbuffer;
@end
@implementation RefusingContext
- (void)preBind:(int)forPbuffer { (void)forPbuffer; }
- (void)makeCurrentContext {}
@end

static int checks = 0;
#define CHECK(condition) do { \
    ++checks; \
    if (!(condition)) { \
        fprintf(stderr, "check %d failed: %s\n", checks, #condition); \
        return 1; \
    } \
} while (0)

int main() {
    @autoreleasepool {
        NSOpenGLPixelFormatAttribute attributes[] = {
            NSOpenGLPFAOpenGLProfile, NSOpenGLProfileVersion4_1Core,
            NSOpenGLPFAAccelerated, NSOpenGLPFAColorSize, 24, 0
        };
        NSOpenGLPixelFormat* format = [[NSOpenGLPixelFormat alloc]
            initWithAttributes:attributes];
        CHECK(format != nil);
        NSOpenGLContext* context = (NSOpenGLContext*)nsCreateContext(format, NULL);
        CHECK(context != nil && [context CGLContextObj] != NULL);
        RefusingContext* refusing = [[RefusingContext alloc]
            initWithFormat:format shareContext:nil];
        CHECK(refusing != nil && [refusing CGLContextObj] != NULL);
        NSView* view = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 16, 16)];

        CHECK(nsWindowMakeCurrent(NULL, NULL));
        CHECK([NSOpenGLContext currentContext] == nil && CGLGetCurrentContext() == NULL);
        // A nil native pbuffer is the supported core-profile path, not an error.
        CHECK(nsPBufferMakeCurrent(context, NULL, 0));
        CHECK([NSOpenGLContext currentContext] == context);
        CHECK(CGLGetCurrentContext() == [context CGLContextObj]);

        CHECK(!nsWindowMakeCurrent(context, NULL));
        CHECK([NSOpenGLContext currentContext] == context);
        CHECK(CGLGetCurrentContext() == [context CGLContextObj]);
        CHECK(!nsPBufferMakeCurrent(refusing, NULL, 0));
        CHECK([NSOpenGLContext currentContext] == context);
        CHECK(CGLGetCurrentContext() == [context CGLContextObj]);
        CHECK(!nsWindowMakeCurrent(refusing, view));
        CHECK([NSOpenGLContext currentContext] == context);
        CHECK(CGLGetCurrentContext() == [context CGLContextObj]);

        CHECK(nsWindowMakeCurrent(context, view));
        CHECK([context view] == view);
        CHECK([NSOpenGLContext currentContext] == context);
        CHECK(CGLGetCurrentContext() == [context CGLContextObj]);
        CHECK(nsPBufferMakeCurrent(NULL, NULL, 0));
        CHECK([NSOpenGLContext currentContext] == nil && CGLGetCurrentContext() == NULL);
        CHECK(!nsPBufferMakeCurrent(refusing, NULL, 0));
        CHECK([NSOpenGLContext currentContext] == nil && CGLGetCurrentContext() == NULL);
        CHECK(nsPBufferMakeCurrent(context, NULL, 0));
        CHECK(nsWindowMakeCurrent(NULL, NULL));

        [context clearDrawable];
        [refusing clearDrawable];
        nsDestroyContext(context);
        [refusing release];
        [view release];
        [format release];
        printf("{\"ok\":true,\"checks\":%d,\"actual_appkit_cgl\":true,"
               "\"nil_core_pbuffer\":true,\"silent_bind_failure\":true,"
               "\"nil_window_rejected\":true,\"unbind_verified\":true}\n", checks);
    }
    return 0;
}
