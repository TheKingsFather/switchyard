// Runs once in KWin. The Python caller supplies the request and receives the reply via D-Bus.
const [receiver, replyPath, token, operation, id, layout] = request;

function reply(payload) {
    callDBus(receiver, replyPath, "io.switchyard.Utility", "receive",
             token, JSON.stringify(payload));
}

function snapshot() {
    return ordinaryWindows().map(window => ({
        id: String(window.internalId),
        title: String(window.caption),
        app: String(window.desktopFileName || window.resourceClass),
        minimized: window.minimized,
        above: window.keepAbove,
        active: window === workspace.activeWindow
    }));
}

function ordinaryWindows() {
    return workspace.stackingOrder.filter(window =>
        window.normalWindow && !window.skipTaskbar && !window.deleted &&
        String(window.resourceClass) !== "io.switchyard.Utility"
    ).reverse();
}

function anchorCompactPanel(pid, screenName) {
    const margin = 10;
    const matches = window => !window.deleted && String(window.pid) === pid &&
        String(window.caption) === "Switchyard panels";

    function place(window) {
        try {
            const output = workspace.screens.find(screen => screen.name === screenName) || window.output;
            const area = workspace.clientArea(KWin.MaximizeArea, output, workspace.currentDesktop);
            const frame = window.frameGeometry;
            window.frameGeometry = {
                x: Math.max(area.x, area.x + area.width - frame.width - margin),
                y: Math.max(area.y, area.y + area.height - frame.height - margin),
                width: frame.width,
                height: frame.height
            };
            reply({});
        } catch (error) {
            reply({error: String(error)});
        }
    }

    const panel = workspace.stackingOrder.find(matches);
    if (panel) {
        place(panel);
    } else {
        function onWindowAdded(window) {
            if (matches(window)) {
                workspace.windowAdded.disconnect(onWindowAdded);
                place(window);
            }
        }
        workspace.windowAdded.connect(onWindowAdded);
    }
}

try {
    if (operation === "list") {
        reply({windows: snapshot()});
    } else if (operation === "anchor-panel") {
        anchorCompactPanel(id, layout);
    } else {
        const window = ordinaryWindows().find(candidate => String(candidate.internalId) === id);
        const shortcuts = {
            left: "Window Quick Tile Left",
            right: "Window Quick Tile Right",
            topLeft: "Window Quick Tile Top Left",
            topRight: "Window Quick Tile Top Right",
            bottomLeft: "Window Quick Tile Bottom Left",
            bottomRight: "Window Quick Tile Bottom Right"
        };
        if (!window) {
            reply({error: "That window has closed. Refresh to update the list."});
        } else if (operation === "activate") {
            window.minimized = false;
            workspace.activeWindow = window;
            reply({});
        } else if (operation === "minimize") {
            window.minimized = true;
            reply({windows: snapshot()});
        } else if (operation === "above") {
            window.keepAbove = !window.keepAbove;
            reply({windows: snapshot()});
        } else if (operation === "layout" && layout === "maximize") {
            window.minimized = false;
            workspace.activeWindow = window;
            window.setMaximize(true, true);
            reply({});
        } else if (operation === "layout" && Object.prototype.hasOwnProperty.call(shortcuts, layout)) {
            window.minimized = false;
            workspace.activeWindow = window;
            callDBus("org.kde.kglobalaccel", "/component/kwin",
                     "org.kde.kglobalaccel.Component", "invokeShortcut",
                     shortcuts[layout], () => reply({}));
        } else {
            reply({error: "Unknown window action."});
        }
    }
} catch (error) {
    reply({error: String(error)});
}
