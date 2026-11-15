import QtQuick
import QtQuick.Controls

Item {
    id: root
    objectName: "marker-" + markerName
    required property var backend
    required property Item viewport
    required property string markerName
    required property string label
    required property int frame
    required property color markerColor
    property bool aboveWaveform: true
    property int lane: 0
    readonly property real viewportHeight: viewport ? viewport.height : 1
    readonly property real laneOffset: Math.max(0, lane) * 48
    readonly property real handleY: aboveWaveform ? 7 : viewportHeight + 7 + laneOffset
    readonly property real lineCenterX: x + width / 2
    x: {
        // Explicit reads make the binding react when frameToX's viewport changes.
        let visibleStart = backend.visibleStart
        let visibleEnd = backend.visibleEnd
        return (viewport ? viewport.x : 0)
               + backend.frameToX(frame, viewport ? viewport.width : 1)
               - width / 2
    }
    y: aboveWaveform ? (viewport ? viewport.y : 54) - 54 - laneOffset
                     : (viewport ? viewport.y : 0)
    width: 140
    height: viewportHeight + 54 + laneOffset
    z: 20

    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        y: root.aboveWaveform ? 54 + root.laneOffset : 0
        width: 3
        height: root.viewportHeight
        color: root.markerColor
    }
    Rectangle {
        id: markerHandle
        anchors.horizontalCenter: parent.horizontalCenter
        y: root.handleY
        width: 136
        height: 40
        radius: 7
        color: root.markerColor
        Label {
            anchors.centerIn: parent
            text: root.label + "  ·  " + backend.timeText(root.frame)
            color: "#081014"
            font.bold: true
            font.pixelSize: 11
        }
    }
    MouseArea {
        id: markerTap
        objectName: "marker-hit-" + root.markerName
        anchors.fill: markerHandle
        cursorShape: Qt.SizeHorCursor
        onPressed: function(mouse) { updateMarker(mouse) }
        onPositionChanged: function(mouse) {
            if (pressed) updateMarker(mouse)
        }
        function updateMarker(mouse) {
            let point = markerTap.mapToItem(root.viewport, mouse.x, mouse.y)
            let value = root.backend.xToFrame(point.x, root.viewport.width)
            if (root.markerName === "selected_loop_start")
                root.backend.setSelectedLoopBoundary("start", value)
            else if (root.markerName === "selected_loop_end")
                root.backend.setSelectedLoopBoundary("end", value)
            else
                root.backend.setMarker(root.markerName, value)
        }
    }
    ToolTip.visible: markerHover.hovered
    ToolTip.text: label + "  " + backend.timeText(frame) + "  ·  Frame " + frame
    HoverHandler { id: markerHover; parent: markerHandle }
}
