import QtQuick
import QtQuick.Controls

Button {
    id: control
    property color baseColor: "#314354"
    property color activeColor: "#55e0c4"
    property color foregroundColor: "#f1f5f8"

    implicitHeight: 44
    leftPadding: 16
    rightPadding: 16
    topPadding: 8
    bottomPadding: 8
    font.pixelSize: 14
    font.weight: Font.Medium

    background: Rectangle {
        radius: 7
        color: !control.enabled ? "#26323d"
              : control.checked ? control.activeColor
              : control.down ? Qt.lighter(control.baseColor, 1.25)
              : control.baseColor
        border.width: control.checked ? 3 : 1
        border.color: control.checked ? Qt.lighter(control.activeColor, 1.35) : "#53697c"
        opacity: control.enabled ? 1.0 : 0.45
    }

    contentItem: Text {
        text: control.text
        font: control.font
        color: control.checked ? "#071512" : control.foregroundColor
        opacity: control.enabled ? 1.0 : 0.55
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}
