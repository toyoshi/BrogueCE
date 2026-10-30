/* Isolated rendered-cell fixture: intentionally no game-state globals linked. */
#define _POSIX_C_SOURCE 200809L
#include "platform.h"
#include "external-control.h"
#include <time.h>
unsigned int glyphToUnicode(enum displayGlyph glyph) { return (unsigned int)glyph; }
int main(int argc, char **argv) {
    if (argc != 2 || !externalControlInit(argv[1])) return 1;
    externalControlPlot('@', 21, 3, 100, 100, 100, 0, 0, 0);
    externalControlPlot('"', 22, 3, 100, 100, 100, 0, 0, 0);
    externalControlPlot('\\', 23, 3, 100, 100, 100, 0, 0, 0);
    externalControlPlot((enum displayGlyph)0x1f780, 24, 3, 100, 100, 100, 0, 0, 0);
    externalControlPlot('X', 25, 3, 0, 0, 0, 0, 0, 0);
    externalControlBeginInput(false);
    for (;;) {
        rogueEvent event;
        if (externalControlPoll(&event)) {
            if (event.eventType == KEYSTROKE && event.param1 == 'q') return 0;
            externalControlBeginInput(false);
        }
        struct timespec pause = {0, 1000000};
        nanosleep(&pause, NULL);
    }
}
