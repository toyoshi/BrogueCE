#ifndef EXTERNAL_CONTROL_H
#define EXTERNAL_CONTROL_H
/* Deliberately depends only on the platform's rendered cells and input events. */
boolean externalControlInit(const char *directory);
void externalControlPlot(enum displayGlyph glyph, short x, short y,
                       short fr, short fg, short fb, short br, short bg, short bb);
void externalControlBeginInput(boolean textInput);
boolean externalControlPoll(rogueEvent *event);
#endif
