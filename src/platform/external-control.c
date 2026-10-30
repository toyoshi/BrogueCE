#define _POSIX_C_SOURCE 200809L
#include "platform.h"
#include "external-control.h"

#if defined(__unix__) || defined(__APPLE__)
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

/* This API reads rendered cells only, never dungeon/entity/identification tables. */
typedef struct {
    unsigned int glyph;
    short rgb[6];
} RenderedCell;
static RenderedCell screen[ROWS][COLS];
static int endpoint = -1;
static char socketPath[sizeof(((struct sockaddr_un *)0)->sun_path)];
static char statePath[1024], session[33];
static unsigned long sequence = 0, lastInput = 0, acceptedInput = 0;
static boolean waiting = false, snapshotReady = false, inputIsText = false;
static boolean pendingMouseUp = false;
static rogueEvent mouseUp;
static time_t nextPublishRetry = 0;

static void controlClose(void) {
    if (endpoint >= 0) {
        close(endpoint);
        unlink(socketPath);
        endpoint = -1;
    }
}

boolean externalControlInit(const char *directory) {
    struct stat info;
    struct sockaddr_un address = {0};
    if (endpoint >= 0 || !directory || directory[0] != '/') {
        fprintf(stderr, "External control: use one absolute, private directory.\n");
        return false;
    }
    if (mkdir(directory, 0700) != 0 && errno != EEXIST) {
        perror("External control mkdir");
        return false;
    }
    if (lstat(directory, &info) != 0 || !S_ISDIR(info.st_mode)
        || info.st_uid != getuid() || (info.st_mode & 077) != 0) {
        fprintf(stderr, "External control: directory must be owned by you, not a symlink, and mode 0700.\n");
        return false;
    }
    if (snprintf(socketPath, sizeof(socketPath), "%s/input.sock", directory) >= sizeof(socketPath)
        || snprintf(statePath, sizeof(statePath), "%s/state.json", directory) >= sizeof(statePath)) {
        fprintf(stderr, "External control: directory path is too long for a Unix socket.\n");
        return false;
    }
    /* Independent OS entropy: never advance the gameplay or cosmetic RNG. */
    unsigned char entropy[16];
    FILE *random = fopen("/dev/urandom", "rb");
    if (!random) return false;
    size_t count = fread(entropy, 1, sizeof(entropy), random);
    fclose(random);
    if (count != sizeof(entropy)) return false;
    for (size_t i = 0; i < sizeof(entropy); i++) sprintf(session + 2 * i, "%02x", entropy[i]);

    endpoint = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (endpoint < 0) return false;
    address.sun_family = AF_UNIX;
    strcpy(address.sun_path, socketPath);
    /* Never unlink a pre-existing socket: another game might own it. */
    if (bind(endpoint, (struct sockaddr *)&address, sizeof(address)) != 0) {
        perror("External control bind");
        close(endpoint);
        endpoint = -1;
        return false;
    }
    if (fcntl(endpoint, F_SETFL, O_NONBLOCK) < 0 || chmod(socketPath, 0600) != 0) {
        perror("External control socket");
        controlClose();
        return false;
    }
    atexit(controlClose);
    return true;
}

void externalControlPlot(enum displayGlyph glyph, short x, short y,
                         short fr, short fg, short fb, short br, short bg, short bb) {
    if (endpoint < 0 || x < 0 || x >= COLS || y < 0 || y >= ROWS) return;
    RenderedCell *cell = &screen[y][x];
    cell->glyph = (fr == br && fg == bg && fb == bb) ? ' ' : glyphToUnicode(glyph);
    cell->rgb[0] = fr; cell->rgb[1] = fg; cell->rgb[2] = fb;
    cell->rgb[3] = br; cell->rgb[4] = bg; cell->rgb[5] = bb;
}

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

static boolean publishSnapshot(void) {
    char tempPath[1100];
    snprintf(tempPath, sizeof(tempPath), "%s.XXXXXX", statePath);
    int fd = mkstemp(tempPath);
    if (fd < 0) return false;
    FILE *out = fdopen(fd, "w");
    if (!out) { close(fd); unlink(tempPath); return false; }
    fprintf(out, "{\"schema_version\":1,\"scope\":\"rendered_screen_only\","
            "\"session\":\"%s\",\"sequence\":%lu,\"last_input_id\":%lu,"
            "\"input_pending\":%s,\"text_input\":%s,"
            "\"width\":%d,\"height\":%d,\"lines\":", session, sequence + 1, lastInput,
            pendingMouseUp ? "true" : "false", inputIsText ? "true" : "false", COLS, ROWS);
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
            RenderedCell *cell = &screen[y][x];
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
        unlink(tempPath);
        return false;
    }
    sequence++; /* Only accept inputs against a successfully published observation. */
    return true;
}

void externalControlBeginInput(boolean textInput) {
    if (endpoint < 0) return;
    waiting = true;
    inputIsText = textInput;
    snapshotReady = publishSnapshot();
    nextPublishRetry = time(NULL) + 1;
    if (!snapshotReady) perror("External control snapshot");
}

/* Bounded decimal parsing: sscanf's integer conversions can overflow. */
static boolean parseNumber(const char *text, unsigned long limit, unsigned long *value) {
    if (!text || !*text) return false;
    unsigned long n = 0;
    for (; *text; text++) {
        if (*text < '0' || *text > '9') return false;
        unsigned int digit = *text - '0';
        if (digit > limit || n > (limit - digit) / 10) return false;
        n = n * 10 + digit;
    }
    *value = n;
    return true;
}

boolean externalControlPoll(rogueEvent *event) {
    if (endpoint < 0 || !waiting) return false;
    if (!snapshotReady && time(NULL) >= nextPublishRetry) {
        snapshotReady = publishSnapshot();
        nextPublishRetry = time(NULL) + 1;
    }
    /* Complete a click before accepting another command, exactly as down/up UI events. */
    if (pendingMouseUp) {
        *event = mouseUp;
        pendingMouseUp = false;
        lastInput = acceptedInput;
        waiting = false;
        return true;
    }
    char request[160], reply[256];
    struct sockaddr_un peer = {0};
    socklen_t length = sizeof(peer);
    ssize_t size = recvfrom(endpoint, request, sizeof(request) - 1, 0,
                            (struct sockaddr *)&peer, &length);
    if (size < 0) return false;
    const char *error = "invalid_command";
    boolean valid = false, click = false;
    unsigned long expected = 0, key = 0, ctrl = 0, shift = 0, x = 0, y = 0;
    char *expectedSession = NULL;
    rogueEvent input = {0};
    if (size < sizeof(request) - 1 && !memchr(request, 0, (size_t)size)) {
        request[size] = '\0';
        char *tokens[8], *save = NULL;
        int count = 0;
        for (char *word = strtok_r(request, " \t\r\n", &save); word && count < 8;
             word = strtok_r(NULL, " \t\r\n", &save)) tokens[count++] = word;
        if (count == 6 && !strcmp(tokens[0], "key")) {
            valid = parseNumber(tokens[1], UP_ARROW + 3, &key)
                && parseNumber(tokens[2], 1, &ctrl) && parseNumber(tokens[3], 1, &shift)
                && parseNumber(tokens[5], ULONG_MAX, &expected)
                && ((key >= 32 && key <= 126) || key == ESCAPE_KEY || key == RETURN_KEY
                    || key == TAB_KEY || key == DELETE_KEY || key == UP_ARROW || key == DOWN_ARROW
                    || key == LEFT_ARROW || key == RIGHT_ARROW);
            expectedSession = tokens[4];
            input.eventType = KEYSTROKE;
            input.param1 = key;
            input.controlKey = ctrl;
            input.shiftKey = shift;
        } else if (count == 5 && (!strcmp(tokens[0], "click") || !strcmp(tokens[0], "hover"))) {
            valid = parseNumber(tokens[1], COLS - 1, &x) && parseNumber(tokens[2], ROWS - 1, &y)
                && parseNumber(tokens[4], ULONG_MAX, &expected);
            expectedSession = tokens[3];
            click = !strcmp(tokens[0], "click");
            input.eventType = click ? MOUSE_DOWN : MOUSE_ENTERED_CELL;
            input.param1 = x;
            input.param2 = y;
        }
    }
    if (valid && strcmp(expectedSession, session)) error = "stale_session";
    else if (valid && !snapshotReady) error = "state_unavailable";
    else if (valid && expected != sequence) error = "stale_sequence";
    else if (valid) error = NULL;
    if (error) snprintf(reply, sizeof(reply), "{\"ok\":false,\"error\":\"%s\"}", error);
    else {
        *event = input;
        acceptedInput++;
        if (click) {
            mouseUp = input;
            mouseUp.eventType = MOUSE_UP;
            pendingMouseUp = true;
        } else lastInput = acceptedInput;
        waiting = false;
        snprintf(reply, sizeof(reply), "{\"ok\":true,\"session\":\"%s\",\"input_id\":%lu,\"sequence\":%lu}",
                 session, acceptedInput, sequence);
    }
    sendto(endpoint, reply, strlen(reply), 0, (struct sockaddr *)&peer, length);
    return error == NULL;
}
#else
boolean externalControlInit(const char *directory) { (void)directory; return false; }
void externalControlPlot(enum displayGlyph g, short x, short y, short fr, short fg, short fb, short br, short bg, short bb) {}
void externalControlBeginInput(boolean textInput) {}
boolean externalControlPoll(rogueEvent *event) { return false; }
#endif
