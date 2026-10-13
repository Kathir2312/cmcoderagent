package cmcoder.eclipse;

import java.net.URL;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.function.Consumer;

import org.eclipse.core.resources.IContainer;
import org.eclipse.core.resources.IFile;
import org.eclipse.core.resources.IResource;
import org.eclipse.core.resources.ResourcesPlugin;
import org.eclipse.core.runtime.IPath;
import org.eclipse.jface.dialogs.MessageDialog;
import org.eclipse.jface.viewers.LabelProvider;
import org.eclipse.jface.window.Window;
import org.eclipse.swt.widgets.Shell;
import org.eclipse.ui.PlatformUI;
import org.eclipse.ui.dialogs.ElementListSelectionDialog;
import org.eclipse.ui.dialogs.FilteredResourcesSelectionDialog;

import cmcoder.ide.core.Ide;
import cmcoder.ide.core.Protocol;

/** The pickers the chat asks for (H11, H13, H18). UI thread only. */
final class Dialogs {

    /** The clipboard's image as PNG, or null (none, or unreadable). On the UI thread. */
    static byte[] clipboardPng() {
        org.eclipse.swt.dnd.Clipboard clipboard = new org.eclipse.swt.dnd.Clipboard(org.eclipse.swt.widgets.Display.getCurrent());
        try {
            Object contents = clipboard.getContents(org.eclipse.swt.dnd.ImageTransfer.getInstance());
            if (!(contents instanceof org.eclipse.swt.graphics.ImageData)) return null;
            org.eclipse.swt.graphics.ImageLoader loader = new org.eclipse.swt.graphics.ImageLoader();
            loader.data = new org.eclipse.swt.graphics.ImageData[] {(org.eclipse.swt.graphics.ImageData) contents};
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            loader.save(out, org.eclipse.swt.SWT.IMAGE_PNG);
            return out.toByteArray();
        } catch (RuntimeException e) { // a busy clipboard, an odd format: no image
            Activator a = Activator.get();
            if (a != null) a.log("Couldn't read the clipboard's image: " + e);
            return null;
        } finally {
            clipboard.dispose();
        }
    }
    private Dialogs() {}

    private static Shell shell() {
        return PlatformUI.getWorkbench().getModalDialogShellProvider().getShell();
    }

    /** /rewind: which message, then what goes back (and files outside the project?). */
    static void pickRewind(List<Protocol.RewindPoint> points, Consumer<Ide.RewindChoice> chosen) {
        List<Protocol.RewindPoint> newestFirst = new ArrayList<>(points);
        Collections.reverse(newestFirst);
        ElementListSelectionDialog which = new ElementListSelectionDialog(shell(), new LabelProvider() {
            @Override
            public String getText(Object element) {
                Protocol.RewindPoint p = (Protocol.RewindPoint) element;
                String text = p.text == null || p.text.isEmpty() ? "(empty message)" : p.text.replaceAll("\\s+", " ");
                return text + (p.filesChanged > 0 ? "  —  " + p.filesChanged + " file(s) changed since" : "");
            }
        });
        which.setTitle(Brand.product() + ": Rewind");
        which.setMessage("Rewind to before which message?");
        which.setElements(newestFirst.toArray());
        which.setMultipleSelection(false);
        if (which.open() != Window.OK || which.getFirstResult() == null) return;
        Protocol.RewindPoint point = (Protocol.RewindPoint) which.getFirstResult();

        MessageDialog what = new MessageDialog(shell(), Brand.product() + ": Rewind", null,
                "What should go back? (Changes made by Bash commands are not undone.)", MessageDialog.QUESTION, 0,
                "Code and conversation", "Conversation only", "Code only", "Cancel");
        int answer = what.open();
        if (answer < 0 || answer > 2) return;
        boolean code = answer != 1;
        boolean conversation = answer != 2;
        boolean outside = false;
        if (code && !point.outsideFiles.isEmpty()) {
            List<String> shown = point.outsideFiles.subList(0, Math.min(10, point.outsideFiles.size()));
            MessageDialog ask = new MessageDialog(shell(), Brand.product() + ": Rewind", null,
                    point.outsideFiles.size() + " changed file(s) are outside the project. Restore them too?\n\n"
                            + String.join("\n", shown),
                    MessageDialog.WARNING, 1, "Restore them too", "Only the project's files", "Cancel");
            int a = ask.open();
            if (a < 0 || a > 1) return;
            outside = a == 0;
        }
        chosen.accept(new Ide.RewindChoice(point.turn, code, conversation, outside));
    }

    /** The @ button: a file of the project, as a path relative to it. */
    static void attachFile(Path projectDir, Consumer<String> chosen) {
        IContainer root = ResourcesPlugin.getWorkspace().getRoot();
        if (projectDir != null) {
            IContainer[] found = ResourcesPlugin.getWorkspace().getRoot().findContainersForLocationURI(projectDir.toUri());
            if (found.length > 0) root = found[0];
        }
        FilteredResourcesSelectionDialog dialog = new FilteredResourcesSelectionDialog(shell(), false, root, IResource.FILE);
        dialog.setTitle(Brand.product() + ": Attach a file to the message (@)");
        dialog.setInitialPattern("**");
        if (dialog.open() != Window.OK || !(dialog.getFirstResult() instanceof IFile)) return;
        IFile file = (IFile) dialog.getFirstResult();
        IPath location = file.getLocation();
        if (location == null) return;
        Path path = location.toFile().toPath();
        String relative = projectDir != null && path.startsWith(projectDir) ? projectDir.relativize(path).toString() : path.toString();
        chosen.accept(relative.replace('\\', '/'));
    }

    /** An http(s) address in the system browser (Host has checked it). */
    static void openExternal(String url) {
        try {
            PlatformUI.getWorkbench().getBrowserSupport().getExternalBrowser().openURL(new URL(url));
        } catch (Exception e) {
            Activator.error("Could not open " + url, e);
        }
    }
}
