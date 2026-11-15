import QtQuick
import QtQuick.Controls

Item {
    id: root
    required property var backend
    required property Item viewport
    required property Item startMarker
    required property Item endMarker
    required property string regionName
    required property string label
    required property color regionColor

    objectName: "region-" + regionName
    visible: backend !== null && backend.hasTrack && width > 0
    enabled: false
    z: 4

    readonly property real leftPosition: Math.max(
        viewport.x, Math.min(viewport.x + viewport.width, startMarker.lineCenterX))
    readonly property real rightPosition: Math.max(
        viewport.x, Math.min(viewport.x + viewport.width, endMarker.lineCenterX))

    x: leftPosition
    y: viewport.y
    width: Math.max(0, rightPosition - leftPosition)
    height: viewport.height

    Rectangle {
        anchors.fill: parent
        color: root.regionColor
        opacity: 0.16
    }

    Label {
        visible: root.width >= implicitWidth + 16
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 8
        text: root.label
        color: root.regionColor
        font.bold: true
        font.pixelSize: 13
        style: Text.Outline
        styleColor: "#11161d"
    }
}
