/* Isolated rendered-cell fixture: intentionally no game-state globals linked. */
#define _POSIX_C_SOURCE 200809L
#include "platform.h"
#include "visible-bridge.h"
#include <time.h>
unsigned int glyphToUnicode(enum displayGlyph glyph) { return (unsigned int)glyph; }
int main(int argc, char **argv) {
    if (argc != 2 || !visibleBridgeInit(argv[1])) return 1;
    visibleBridgePlot('@', 21, 3, 100, 100, 100, 0, 0, 0);
    visibleBridgePlot('"', 22, 3, 100, 100, 100, 0, 0, 0);
    visibleBridgePlot('\\', 23, 3, 100, 100, 100, 0, 0, 0);
    visibleBridgePlot((enum displayGlyph)0x1f780, 24, 3, 100, 100, 100, 0, 0, 0);
    visibleBridgePlot('X', 25, 3, 0, 0, 0, 0, 0, 0);
    visibleBridgeBeginInput(false);
    for (;;) {
        rogueEvent event;
        if (visibleBridgePoll(&event)) {
            if (event.eventType == KEYSTROKE && event.param1 == 'q') return 0;
            visibleBridgeBeginInput(false);
        }
        struct timespec pause = {0, 1000000};
        nanosleep(&pause, NULL);
    }
}
