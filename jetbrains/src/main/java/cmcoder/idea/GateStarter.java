package cmcoder.idea;

import java.util.List;

import org.jetbrains.annotations.NotNull;

import com.intellij.ide.AppLifecycleListener;

/**
 * Starts the release gate's self-test when the IDE starts with
 * -Dcmcoder.gate=&lt;result file&gt;, in the test build only (-Pgate: the
 * self-test, Gate.java, isn't in a release, so there this does nothing).
 */
public final class GateStarter implements AppLifecycleListener {
    @Override
    public void appFrameCreated(@NotNull List<String> commandLineArgs) {
        String gate = System.getProperty("cmcoder.gate");
        if (gate == null || gate.isBlank() || !Panel.testBuild()) return;
        try {
            Class.forName("cmcoder.idea.Gate").getMethod("start", String.class).invoke(null, gate);
        } catch (ReflectiveOperationException | RuntimeException e) {
            Plugin.error("cmcoder.gate is set, but the gate couldn't start", e);
        }
    }
}
