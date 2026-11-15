import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: root
    required property string title
    property string text: ""
    property string suffix: ""
    property bool readOnly: false
    property bool liveEdit: false
    property bool editing: false
    signal edited(string value)
    signal committed(string value)
    spacing: 8
    Binding {
        target: input
        property: "text"
        value: root.text
        when: !root.editing
        restoreMode: Binding.RestoreNone
    }
    Label { text: root.title; Layout.preferredWidth: 125; color: "#b9c7d6" }
    TextField {
        id: input
        Layout.fillWidth: true
        selectByMouse: true
        readOnly: root.readOnly
        objectName: root.objectName ? root.objectName + "Input" : ""
        onTextEdited: {
            root.editing = true
            if (root.liveEdit)
                root.edited(text)
        }
        onEditingFinished: {
            if (!root.liveEdit)
                root.committed(text)
            root.editing = false
        }
    }
    Label { text: root.suffix; color: "#8293a5" }
}
