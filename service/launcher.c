// haiku-rag-service: the program launchd runs for the local search service.
//
// macOS grants Full Disk Access to a program, and a process's file access is
// checked against the program responsible for it: the one launchd started,
// whose children inherit it. This launcher is that program, at a path that
// does not change when haiku.rag is upgraded: grant it Full Disk Access once,
// and the service it runs as its child reads the indexed folders.
//
// It takes the listening socket launchd holds for the job (socket activation,
// the "Listeners" entry of the plist), hands it to the child as
// HAIKU_RAG_LISTEN_FD, runs the command it is given, forwards SIGTERM, SIGINT
// and SIGHUP to it, and exits with its status.
//
// Build: cc -O2 -o haiku-rag-service launcher.c

#include <errno.h>
#include <fcntl.h>
#include <launch.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static volatile pid_t child = 0;

static void forward(int sig) {
    if (child > 0) {
        kill(child, sig);
    }
}

static void hand_on_socket(void) {
    int *fds = NULL;
    size_t count = 0;
    if (launch_activate_socket("Listeners", &fds, &count) != 0 || count == 0) {
        return;  // not started by launchd with a socket: the service binds its own
    }
    int flags = fcntl(fds[0], F_GETFD);
    fcntl(fds[0], F_SETFD, flags & ~FD_CLOEXEC);
    char value[16];
    snprintf(value, sizeof value, "%d", fds[0]);
    setenv("HAIKU_RAG_LISTEN_FD", value, 1);
    for (size_t i = 1; i < count; i++) {
        close(fds[i]);
    }
    free(fds);
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s COMMAND [ARGUMENT...]\n", argv[0]);
        return 64;
    }
    hand_on_socket();

    struct sigaction action;
    memset(&action, 0, sizeof action);
    action.sa_handler = forward;
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGHUP, &action, NULL);

    pid_t pid;
    int error = posix_spawnp(&pid, argv[1], NULL, NULL, &argv[1], environ);
    if (error != 0) {
        fprintf(stderr, "haiku-rag-service: cannot run %s: %s\n", argv[1], strerror(error));
        return 69;
    }
    child = pid;

    int status;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) {
            perror("haiku-rag-service: waitpid");
            return 70;
        }
    }
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    return 128 + WTERMSIG(status);
}
