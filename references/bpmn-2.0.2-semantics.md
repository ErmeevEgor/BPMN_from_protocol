# Supported BPMN 2.0.2 semantics

Verified 2026-09-02 against the official OMG BPMN 2.0.2 specification page:
https://www.omg.org/spec/BPMN/2.0.2/

- A Process may omit LaneSet; the corporate profile requires no internal Lane.
- Sequence Flow connects Flow Nodes within a Process.
- Message Flow connects different Participants in a Collaboration.
- A Message Flow that starts the included Process targets a Message Start
  Event carrying `messageEventDefinition`, not a None Start Event.
- Call Activity is distinct from a black-box Participant.
- Call Activity carries `calledElement`; a default Sequence Flow is referenced
  by the `default` attribute of its source Gateway/Activity.
- DataObjectReference occurrences may share one logical object and carry
  different DataState values.
- Data Association keeps the Artifact and Activity as its BPMN source/target;
  render-only ports and waypoints do not replace those semantic endpoints.
- Render ports lie on the actual Flow Node perimeter (rectangle, diamond or
  ellipse), and post-render validation checks the visible SVG endpoints.
- Participant `processRef` is emitted only for an included Process definition;
  corporate catalog links remain extension data.
- The supported subset includes User, Manual, Service, Script, Business Rule,
  Send, Receive and Call Activities; Start/End/intermediate events; exclusive,
  inclusive, parallel and event-based gateways.

Runtime extraction uses this local reference and does not query the web.
