import QtQuick
import QtQuick.Controls

Item {
    id: root
    required property var backend
    required property Item viewport
    required property string bandName
    required property string label
    required property int startFrame
    required property int endFrame
    required property color bandColor
    property bool selected: false
    property real laneY: viewport.y + viewport.height - height
    signal tapped()

    objectName: "band-" + bandName
    readonly property real leftPosition: {
        // frameToX() is an invokable method. Read the viewport properties
        // explicitly so QML invalidates this binding after zoom and pan.
        let visibleStart = backend.visibleStart
        let visibleEnd = backend.visibleEnd
        return Math.max(
            viewport.x,
            Math.min(viewport.x + viewport.width,
                     viewport.x + backend.frameToX(startFrame, viewport.width)))
    }
    readonly property real rightPosition: {
        let visibleStart = backend.visibleStart
        let visibleEnd = backend.visibleEnd
        return Math.max(
            viewport.x,
            Math.min(viewport.x + viewport.width,
                     viewport.x + backend.frameToX(endFrame, viewport.width)))
    }

    x: leftPosition
    y: laneY
    width: Math.max(0, rightPosition - leftPosition)
    height: 28
    visible: backend.hasTrack && width > 0
    z: selected ? 10 : 7

    Rectangle {
        anchors.fill: parent
        radius: 5
        color: root.bandColor
        opacity: root.selected ? 0.82 : 0.48
        border.color: root.selected ? "#ffffff" : root.bandColor
        border.width: root.selected ? 2 : 1
    }
    Label {
        anchors.centerIn: parent
        width: Math.max(0, parent.width - 10)
        text: root.label
        color: "#091016"
        font.bold: true
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignHCenter
    }
    MouseArea { anchors.fill: parent; onClicked: root.tapped() }
}
