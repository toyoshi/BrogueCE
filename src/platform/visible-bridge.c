#define _POSIX_C_SOURCE 200809L
#include "platform.h"
#include "visible-bridge.h"

#if defined(__unix__) || defined(__APPLE__)
#include <errno.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

/* No Globals.h, pmap, creatures, item tables, RNG, or save data are read here. */
typedef struct {
    unsigned int glyph;
    short rgb[6];
} VisibleCell;
static VisibleCell screen[ROWS][COLS];
static int endpoint = -1;
static char socketPath[sizeof(((struct sockaddr_un *)0)->sun_path)];
static char statePath[1024];
static unsigned long sequence = 0, lastInput = 0;
static boolean waiting = false;

static void bridgeClose(void) {
    if (endpoint >= 0) {
        close(endpoint);
        unlink(socketPath);
        endpoint = -1;
    }
}

boolean visibleBridgeInit(const char *directory) {
    struct stat info;
    struct sockaddr_un address = {0};
    if (endpoint >= 0 || !directory || directory[0] != '/') {
        fprintf(stderr, "Bridge: use one absolute, private directory.\n");
        return false;
    }
    if (mkdir(directory, 0700) != 0 && errno != EEXIST) {
        perror("Bridge mkdir");
        return false;
    }
    if (lstat(directory, &info) != 0 || !S_ISDIR(info.st_mode)
        || info.st_uid != getuid() || (info.st_mode & 077) != 0) {
        fprintf(stderr, "Bridge: directory must be owned by you, not a symlink, and mode 0700.\n");
        return false;
    }
    if (snprintf(socketPath, sizeof(socketPath), "%s/input.sock", directory) >= sizeof(socketPath)
        || snprintf(statePath, sizeof(statePath), "%s/state.json", directory) >= sizeof(statePath)) {
        fprintf(stderr, "Bridge: directory path is too long for a Unix socket.\n");
        return false;
    }
    endpoint = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (endpoint < 0) return false;
    address.sun_family = AF_UNIX;
    strcpy(address.sun_path, socketPath);
    /* Never unlink a pre-existing socket: another game might own it. */
    if (bind(endpoint, (struct sockaddr *)&address, sizeof(address)) != 0
        || fcntl(endpoint, F_SETFL, O_NONBLOCK) < 0) {
        perror("Bridge socket");
        close(endpoint);
        endpoint = -1;
        return false;
    }
    chmod(socketPath, 0600);
    atexit(bridgeClose);
    return true;
}

void visibleBridgePlot(enum displayGlyph glyph, short x, short y,
                       short fr, short fg, short fb, short br, short bg, short bb) {
    if (endpoint < 0 || x < 0 || x >= COLS || y < 0 || y >= ROWS) return;
    VisibleCell *cell = &screen[y][x];
    /* Invisible foreground must not reveal a hidden character. */
    cell->glyph = (fr == br && fg == bg && fb == bb) ? ' ' : glyphToUnicode(glyph);
    cell->rgb[0] = fr; cell->rgb[1] = fg; cell->rgb[2] = fb;
    cell->rgb[3] = br; cell->rgb[4] = bg; cell->rgb[5] = bb;
}

/* JSON escaping by code point avoids both locale dependence and invalid UTF-8. */
static void writeGlyph(FILE *out, unsigned int c) {
    if (c == 0) c = ' ';
    if (c == '"' || c == '\\') fprintf(out, "\\%c", (char)c);
    else if (c >= 32 && c < 127) fputc(c, out);
    else if (c <= 0xffff) fprintf(out, "\\u%04x", c);
    else if (c <= 0x10ffff) {
        c -= 0x10000;
        fprintf(out, "\\u%04x\\u%04x", 0xd800 + (c >> 10), 0xdc00 + (c & 1023));
    } else fputs("\\ufffd", out);
}

static void writeLines(FILE *out, int x0, int y0, int width, int height) {
    fputc('[', out);
    for (int y = y0; y < y0 + height; y++) {
        if (y != y0) fputc(',', out);
        fputc('"', out);
        for (int x = x0; x < x0 + width; x++) writeGlyph(out, screen[y][x].glyph);
        fputc('"', out);
    }
    fputc(']', out);
}

void visibleBridgeBeginInput(boolean textInput) {
    if (endpoint < 0) return;
    waiting = true;
    sequence++;
    char tempPath[1100];
    snprintf(tempPath, sizeof(tempPath), "%s.XXXXXX", statePath);
    int fd = mkstemp(tempPath);
    if (fd < 0) { perror("Bridge snapshot"); return; }
    FILE *out = fdopen(fd, "w");
    if (!out) { close(fd); unlink(tempPath); return; }
    fprintf(out, "{\"schema_version\":1,\"scope\":\"rendered_screen_only\","
            "\"sequence\":%lu,\"last_input_id\":%lu,\"text_input\":%s,"
            "\"width\":%d,\"height\":%d,\"lines\":", sequence, lastInput,
            textInput ? "true" : "false", COLS, ROWS);
    writeLines(out, 0, 0, COLS, ROWS);
    fputs(",\"sidebar\":", out);
    writeLines(out, 0, 0, STAT_BAR_WIDTH, ROWS);
    fputs(",\"messages\":", out);
    writeLines(out, STAT_BAR_WIDTH + 1, 0, DCOLS, MESSAGE_LINES);
    fprintf(out, ",\"viewport\":{\"screen_x\":%d,\"screen_y\":%d,\"width\":%d,\"height\":%d,\"lines\":",
            STAT_BAR_WIDTH + 1, MESSAGE_LINES, DCOLS, DROWS);
    writeLines(out, STAT_BAR_WIDTH + 1, MESSAGE_LINES, DCOLS, DROWS);
    fputs("},\"cell_format\":[\"glyph\",\"fg_r\",\"fg_g\",\"fg_b\",\"bg_r\",\"bg_g\",\"bg_b\"],\"cells\":[", out);
    for (int y = 0; y < ROWS; y++) {
        if (y) fputc(',', out);
        fputc('[', out);
        for (int x = 0; x < COLS; x++) {
            VisibleCell *cell = &screen[y][x];
            if (x) fputc(',', out);
            fputs("[\"", out);
            writeGlyph(out, cell->glyph);
            fputc('"', out);
            for (int c = 0; c < 6; c++) fprintf(out, ",%d", cell->rgb[c]);
            fputc(']', out);
        }
        fputc(']', out);
    }
    fputs("]}\n", out);
    boolean failed = ferror(out);
    if (fclose(out) != 0) failed = true;
    if (failed || rename(tempPath, statePath) != 0) {
        perror("Bridge snapshot publish");
        unlink(tempPath);
    }
}

boolean visibleBridgePoll(rogueEvent *event) {
    if (endpoint < 0 || !waiting) return false;
    char request[128], reply[160], trailing;
    struct sockaddr_un peer;
    socklen_t length = sizeof(peer);
    ssize_t size = recvfrom(endpoint, request, sizeof(request) - 1, 0,
                            (struct sockaddr *)&peer, &length);
    if (size < 0) return false;
    if (size == sizeof(request) - 1 || memchr(request, 0, (size_t)size)) {
        const char *error = "{\"ok\":false,\"error\":\"invalid_command\"}";
        sendto(endpoint, error, strlen(error), 0, (struct sockaddr *)&peer, length);
        return false;
    }
    request[size] = '\0';
    long key = 0, control = 0, shift = 0, x = 0, y = 0;
    unsigned long expected = 0;
    boolean valid = false;
    rogueEvent input = {0};
    if (sscanf(request, "key %ld %ld %ld %lu %c", &key, &control, &shift, &expected, &trailing) == 4) {
        valid = ((key >= 32 && key <= 126) || key == ESCAPE_KEY || key == RETURN_KEY
                 || key == TAB_KEY || key == DELETE_KEY || key == UP_ARROW || key == DOWN_ARROW
                 || key == LEFT_ARROW || key == RIGHT_ARROW)
                && (control == 0 || control == 1) && (shift == 0 || shift == 1);
        input.eventType = KEYSTROKE;
        input.param1 = key;
        input.controlKey = control;
        input.shiftKey = shift;
    } else {
        char kind[8];
        if (sscanf(request, "%7s %ld %ld %lu %c", kind, &x, &y, &expected, &trailing) == 4
            && (!strcmp(kind, "click") || !strcmp(kind, "hover"))) {
            valid = x >= 0 && x < COLS && y >= 0 && y < ROWS;
            input.eventType = !strcmp(kind, "click") ? MOUSE_UP : MOUSE_ENTERED_CELL;
            input.param1 = x;
            input.param2 = y;
        }
    }
    if (!valid) snprintf(reply, sizeof(reply), "{\"ok\":false,\"error\":\"invalid_command\"}");
    else if (expected != sequence) snprintf(reply, sizeof(reply),
            "{\"ok\":false,\"error\":\"stale_sequence\",\"sequence\":%lu}", sequence);
    else {
        *event = input;
        lastInput++;
        waiting = false;
        snprintf(reply, sizeof(reply), "{\"ok\":true,\"input_id\":%lu,\"sequence\":%lu}", lastInput, sequence);
    }
    sendto(endpoint, reply, strlen(reply), 0, (struct sockaddr *)&peer, length);
    return valid && expected == sequence;
}
#else
boolean visibleBridgeInit(const char *directory) { (void)directory; return false; }
void visibleBridgePlot(enum displayGlyph g, short x, short y, short fr, short fg, short fb, short br, short bg, short bb) {}
void visibleBridgeBeginInput(boolean textInput) {}
boolean visibleBridgePoll(rogueEvent *event) { return false; }
#endif
