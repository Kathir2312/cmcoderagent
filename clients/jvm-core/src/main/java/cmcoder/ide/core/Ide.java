package cmcoder.ide.core;

import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * What an IDE does for {@link Host}: everything that needs the IDE's own APIs.
 * The Eclipse and NetBeans plugins each implement it; the rest of the host
 * duties live in {@link Host}, the same for both.
 *
 * <p>Host calls these from its own threads and never waits for them: an
 * implementation hands UI work to the IDE's UI thread and returns.
 */
public interface Ide {
    /** Deliver a message (JSON) to the chat page: {@code window.postMessage(message, "*")} in it. */
    void toPanel(String json);

    /** Deliver a message to the Agent Navigator page, if it is open. */
    void toNavigator(String json);

    /** Open (or show) the Agent Navigator tab (H15). */
    void openNavigator();

    /** Bring the chat panel to the front. */
    void focusChat();

    /** The editor context now (H7; see {@link EditorContext}), or null when no file is open. */
    Map<String, Object> editorContext();

    /** The setting "send the editor context with each message". */
    boolean autoContext();

    /** The setting "review changes in the diff viewer" (H9). */
    boolean diffReview();

    /** Show a proposed change in the IDE's diff viewer, with Accept and Reject (H9). */
    void openDiff(String requestId, Protocol.FileChange change);

    /** Close the diff for a request (answered elsewhere, or the turn ended). */
    void closeDiff(String requestId);

    /** The IDE tools this IDE can run (H8), e.g. getDiagnostics, openFile. */
    List<String> ideTools();

    /** Run an IDE tool; call {@code done} with (content, isError) on any thread. */
    void runIdeTool(String name, Map<String, Object> input, ToolDone done);

    interface ToolDone {
        void done(String content, boolean isError);
    }

    /** /rewind: let the user choose a point and what to undo (H11); call {@code chosen} only if they did. */
    void pickRewind(List<Protocol.RewindPoint> points, Consumer<RewindChoice> chosen);

    final class RewindChoice {
        public final long turn;
        public final boolean code;
        public final boolean conversation;
        public final boolean outside;

        public RewindChoice(long turn, boolean code, boolean conversation, boolean outside) {
            this.turn = turn;
            this.code = code;
            this.conversation = conversation;
            this.outside = outside;
        }
    }

    /** The @ button: let the user pick a project file; call {@code chosen} with its path relative to the project (H13). */
    void attachFile(Consumer<String> chosen);

    /**
     * The system clipboard's image as PNG bytes, or null when it holds none. For
     * browsers that don't give the page clipboard images (JavaFX's); others don't
     * need it. Called on the host's thread; may answer on any thread.
     */
    default void clipboardImage(Consumer<byte[]> png) {
        png.accept(null);
    }

    /** Open an http(s) address in the system browser (H18; Host has already checked it). */
    void openExternal(String url);

    /** A line for the plugin's log (cmcoder's stderr, starts and ends). */
    void log(String line);
}
