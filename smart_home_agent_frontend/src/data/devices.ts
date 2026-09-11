// Device id → display name + authoritative location hint, used for floor-map
// bubble headers/captions. Source of truth in production is the backend
// (configs/environment/Offis_smart_studio.yaml); mirrored here for the UI.

export interface DeviceInfo {
  name: string;
  hint: string;
}

export const DEVICES: Record<string, DeviceInfo> = {
  doorlight: { name: 'Door Light', hint: 'Between the door and the TV, on the wall.' },
  windowlight: { name: 'Window Light', hint: 'Between the two windows, on the wall.' },
  floorlamp: { name: 'Floor Lamp', hint: 'To the right of the TV, next to the plants.' },
  bedlight_l: { name: 'Bedlight Left', hint: 'Left side of the bed, beside the smart fan.' },
  bedlight_r: { name: 'Bedlight Right', hint: 'Right side of the bed, towards the closet.' },
  socket_tv: { name: 'TV Smart Plug', hint: 'Behind the TV unit.' },
  tv: { name: 'TV', hint: 'On the TV unit on the far wall.' },
  socket_fan: { name: 'Fan Smart Plug', hint: 'Behind the fan, connected to the wall socket.' },
  smartfan: { name: 'Smart Fan', hint: 'Left side of the bed, next to the left bedlight.' },
  heater: { name: 'Heater', hint: 'Below the shuttered window, behind the sofa.' },
  temperaturesensor: { name: 'Temperature Sensor', hint: 'Bottom-right corner of the room.' },
  presencesensor: { name: 'Presence Sensor', hint: 'On the ceiling, in the middle of the room.' },
  doorsensor: { name: 'Door Sensor', hint: 'Above the left door frame.' },
  windowsensor: { name: 'Window Sensor', hint: 'Above the left window frame.' },
  rollo: { name: 'Roller Shutter', hint: 'Installed above the left window.' },
  curtain: { name: 'Curtain', hint: "Motor on the right window's curtain rail." },
  ir_blaster: { name: 'IR Blaster', hint: 'On the TV cabinet.' },
  smart_hub: { name: 'Smart Hub', hint: 'In the left wall cabinet.' },
};

/** Door sensor has no dedicated highlight asset → falls back to base plan. */
export function floorplanFile(deviceId: string): string {
  return deviceId === 'doorsensor' ? 'base' : deviceId;
}

/** Local fallback floor-plan URL (served from public/assets/floorplans). */
export function localFloorplanUrl(deviceId: string): string {
  return `/assets/floorplans/${floorplanFile(deviceId)}.png`;
}
