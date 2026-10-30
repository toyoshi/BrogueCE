#ifndef VISIBLE_BRIDGE_H
#define VISIBLE_BRIDGE_H
/* Deliberately depends only on the platform's rendered cells and input events. */
boolean visibleBridgeInit(const char *directory);
void visibleBridgePlot(enum displayGlyph glyph, short x, short y,
                       short fr, short fg, short fb, short br, short bg, short bb);
void visibleBridgeBeginInput(boolean textInput);
boolean visibleBridgePoll(rogueEvent *event);
#endif
