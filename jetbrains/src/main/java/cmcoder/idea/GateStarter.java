package cmcoder.idea;

import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import com.intellij.ide.AppLifecycleListener;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.startup.ProjectActivity;

import kotlin.Unit;
import kotlin.coroutines.Continuation;

/**
 * Starts the release gate's self-test when the IDE starts with
 * -Dcmcoder.gate=&lt;result file&gt;, in the test build only (-Pgate: the
 * self-test, Gate.java, isn't in a release, so there this does nothing).
 * Started from whichever comes first, the app's frame or a project opening
 * (newer IDEs don't announce the frame); once.
 */
public final class GateStarter implements AppLifecycleListener, ProjectActivity {
    private static final AtomicBoolean started = new AtomicBoolean();

    @Override
    public void appFrameCreated(@NotNull List<String> commandLineArgs) {
        start();
    }

    @Override
    public @Nullable Object execute(@NotNull Project project, @NotNull Continuation<? super Unit> continuation) {
        start();
        return Unit.INSTANCE;
    }

    private static void start() {
        String gate = System.getProperty("cmcoder.gate");
        if (gate == null || gate.isBlank() || !started.compareAndSet(false, true)) return;
        Plugin.log("cmcoder gate: starting the self-test");
        try {
            // Only in the test build (-Pgate): never in a release.
            Class.forName("cmcoder.idea.Gate").getMethod("start", String.class).invoke(null, gate);
        } catch (ReflectiveOperationException | RuntimeException | LinkageError e) {
            Plugin.error("cmcoder.gate is set, but the gate couldn't start (not the test build?)", e);
        }
    }
}
