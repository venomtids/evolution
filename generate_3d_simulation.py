"""
generate_3d_simulation.py
Gera vídeo de simulação 3D FÍSICA (MuJoCo) com um BRAÇO ROBÔTICO INDUSTRIAL
executando ciclo pick-and-place numa célula robotizada.

A física é real: juntas com dinâmica, gravidade e atuadores de posição.
A "falta" escala por fase (como o vídeo 2D) injetando ressonância no cotovelo
e pulso + afrouxando o ganho do ombro — a vibração cresce de verdade:
    NORMAL 0–40%  -> controle firme, sem perturbação
    ALERTA 40–73% -> perturbação 6 Hz, ganho menor
    CRITICO (restante) -> perturbação 10 Hz forte, ombro com torque de queda

Cenário: célula com pedestal, turret (yaw), ombro, cotovelo, pulso e garra
de 2 dedos; bancada de peças, armário com E-Stop, torre de sinalização,
cerca de segurança e faixa de piso.

Gera sidecar <nome>.telemetry.jsonl (frame/seconds/phase/temperature/rpm).

Uso:
    python generate_3d_simulation.py --output captures/simulation_3d.mp4 --duration 60 --fps 30
"""

import argparse
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import mujoco

from generate_simulation import temperature_at, rpm_at

W, H = 640, 480

MJCF = """
<mujoco model="evo_arm">
  <option timestep="0.002" gravity="0 0 -9.81" integrator="RK4"/>
  <compiler angle="radian" meshdir="__MESHDIR__"/>

  <visual>
    <global offwidth="640" offheight="480"/>
    <headlight diffuse="0.35 0.35 0.35" ambient="0.35 0.35 0.35"/>
  </visual>

  <asset>
    <mesh name="m_base" file="base.stl"/>
    <mesh name="m_turret" file="turret.stl"/>
    <mesh name="m_upper" file="upper.stl"/>
    <mesh name="m_forearm" file="forearm.stl"/>
    <mesh name="m_tool" file="tool.stl"/>
    <material name="floor_mat" rgba="0.32 0.33 0.36 1" specular="0.25" shininess="0.35"/>
    <material name="yellow"    rgba="0.78 0.62 0.06 1" specular="0.3"  shininess="0.4"/>
    <material name="orange"    rgba="0.86 0.42 0.07 1" specular="0.55" shininess="0.75"/>
    <material name="dark"      rgba="0.20 0.21 0.24 1" specular="0.3"  shininess="0.4"/>
    <material name="black"     rgba="0.10 0.10 0.12 1" specular="0.2"/>
    <material name="steel"     rgba="0.74 0.75 0.80 1" specular="0.85" shininess="0.85"/>
    <material name="amber_part" rgba="0.95 0.68 0.15 1" specular="0.6" shininess="0.7"/>
    <material name="screen_on" rgba="0.16 0.85 0.95 1" emission="0.9"/>
    <material name="ok_btn"    rgba="0.12 0.90 0.30 1" emission="0.9"/>
    <material name="estop_btn" rgba="0.95 0.12 0.10 1" emission="0.9"/>
    <material name="lamp_g_on"  rgba="0.10 0.95 0.25 1" emission="0.95"/>
    <material name="lamp_g_off" rgba="0.03 0.28 0.08 1"/>
    <material name="lamp_a_on"  rgba="1.00 0.72 0.05 1" emission="0.95"/>
    <material name="lamp_a_off" rgba="0.35 0.24 0.02 1"/>
    <material name="lamp_r_on"  rgba="1.00 0.11 0.08 1" emission="0.95"/>
    <material name="lamp_r_off" rgba="0.32 0.04 0.03 1"/>
    <material name="bin"       rgba="0.36 0.30 0.16 1" specular="0.3"/>
    <material name="table"     rgba="0.26 0.28 0.32 1" specular="0.3"/>
  </asset>

  <worldbody>
    <light pos="0.4 -0.6 3.6" dir="0.2 0.25 -1" diffuse="0.85 0.85 0.85" specular="0.3 0.3 0.3"/>
    <light pos="-2.5 2.0 2.5" dir="0.35 -0.3 -1" diffuse="0.45 0.45 0.5"/>
    <camera name="overview" pos="3.1 6.3 3.6" mode="targetbody" target="turret"/>

    <geom name="floor" type="plane" size="8 8 0.1" material="floor_mat" contype="0" conaffinity="0"/>
    <geom name="strip1" type="box" size="0.045 3.4 0.012" pos="-2.15 0 0.012" material="yellow" contype="0" conaffinity="0"/>
    <geom name="strip2" type="box" size="0.045 3.4 0.012" pos="2.15 0 0.012" material="yellow" contype="0" conaffinity="0"/>

    <geom name="pedestal" type="cylinder" size="0.46 0.42" pos="0 0 0.42" material="dark" contype="0" conaffinity="0"/>

    <geom name="arm_base" type="mesh" mesh="m_base" pos="0 0 0.84" material="dark" contype="0" conaffinity="0"/>
    <body name="turret" pos="0 0 0.84">
      <joint name="j_yaw" type="hinge" axis="0 0 1" range="-1.30 1.30" damping="12" frictionloss="2" armature="1.5"/>
      <geom name="mturret" type="mesh" mesh="m_turret" material="orange" mass="34" contype="0" conaffinity="0"/>
      <body name="shoulder" pos="0.016 -0.0305 0.410">
        <joint name="j_shoulder" type="hinge" axis="1 0 0" range="0.10 1.85" damping="9" frictionloss="1.5" armature="2.5"/>
        <geom name="mupper" type="mesh" mesh="m_upper" material="orange" mass="31" contype="0" conaffinity="0"/>
        <body name="elbow" pos="0.030 0.120 0.610">
          <joint name="j_elbow" type="hinge" axis="1 0 0" range="-2.45 -0.05" damping="6.5" frictionloss="1.2" armature="1.6"/>
          <geom name="mforearm" type="mesh" mesh="m_forearm" material="orange" mass="9" contype="0" conaffinity="0"/>
          <body name="wrist" pos="0.425 0.050 -0.090">
            <joint name="j_wrist" type="hinge" axis="1 0 0" range="-2.5 2.5" damping="3.5" frictionloss="0.8" armature="0.9"/>
            <geom name="mtool" type="mesh" mesh="m_tool" material="steel" mass="8" contype="0" conaffinity="0"/>
          </body>
        </body>
      </body>
    </body>
    <geom name="table_top" type="box" size="0.44 0.028 0.28" pos="1.02 1.30 0.74" material="table" contype="0" conaffinity="0"/>
    <geom name="table_leg" type="box" size="0.34 0.34 0.24" pos="1.02 1.30 0.36" material="table" contype="0" conaffinity="0"/>
    <geom name="part1" type="cylinder" size="0.055 0.075" pos="0.85 1.38 0.845" material="amber_part" contype="0" conaffinity="0"/>
    <geom name="part2" type="cylinder" size="0.055 0.075" pos="1.04 1.20 0.845" material="amber_part" contype="0" conaffinity="0"/>
    <geom name="part3" type="cylinder" size="0.055 0.075" pos="1.19 1.40 0.845" material="amber_part" contype="0" conaffinity="0"/>

    <geom name="bin" type="box" size="0.30 0.16 0.19" pos="-1.02 1.30 0.16" material="bin" contype="0" conaffinity="0"/>

    <geom name="cabinet" type="box" size="0.42 0.42 0.62" pos="1.95 0.55 0.62" material="dark" contype="0" conaffinity="0"/>
    <geom name="screen" type="box" size="0.02 0.17 0.26" pos="1.53 0.55 0.95" material="screen_on" contype="0" conaffinity="0"/>
    <geom name="btn_ok" type="cylinder" size="0.035 0.015" pos="1.53 0.30 0.78" euler="0 1.5708 0" material="ok_btn" contype="0" conaffinity="0"/>
    <geom name="btn_estop" type="cylinder" size="0.045 0.02" pos="1.53 0.80 0.78" euler="0 1.5708 0" material="estop_btn" contype="0" conaffinity="0"/>

    <geom name="lamp_pole" type="cylinder" size="0.045 0.62" pos="-1.92 -1.05 1.36" material="steel" contype="0" conaffinity="0"/>
    <geom name="lamp_green" type="cylinder" size="0.085 0.075" pos="-1.92 -1.05 2.10" material="lamp_g_on" contype="0" conaffinity="0"/>
    <geom name="lamp_amber" type="cylinder" size="0.085 0.075" pos="-1.92 -1.05 2.26" material="lamp_a_off" contype="0" conaffinity="0"/>
    <geom name="lamp_red" type="cylinder" size="0.085 0.075" pos="-1.92 -1.05 2.42" material="lamp_r_off" contype="0" conaffinity="0"/>

    <geom name="fence_p1" type="box" size="0.05 0.55 0.05" pos="-2.4 2.9 0.55" material="yellow" contype="0" conaffinity="0"/>
    <geom name="fence_p2" type="box" size="0.05 0.55 0.05" pos="2.4 2.9 0.55" material="yellow" contype="0" conaffinity="0"/>
    <geom name="fence_rail" type="box" size="2.4 0.035 0.035" pos="0 2.9 0.85" material="yellow" contype="0" conaffinity="0"/>
  </worldbody>

  <actuator>
    <position name="a_yaw"      joint="j_yaw"      kp="800" kv="80" ctrlrange="-1.4 1.4"/>
    <position name="a_shoulder" joint="j_shoulder" kp="3200" kv="240" ctrlrange="0.1 2.0"/>
    <position name="a_elbow"    joint="j_elbow"    kp="1150" kv="100" ctrlrange="-2.5 -0.05"/>
    <position name="a_wrist"    joint="j_wrist"    kp="380" kv="45" ctrlrange="-2.5 2.5"/>
  </actuator>
</mujoco>
"""


# ---------------- Fase / controle ----------------

def phase_of(frame_idx: int, total_frames: int) -> str:
    n_normal = int(total_frames * 0.40)
    n_alert = int(total_frames * 0.33)
    if frame_idx < n_normal:
        return "normal"
    if frame_idx < n_normal + n_alert:
        return "alerta"
    return "critico"


def cycle_targets(t: float, phase: str):
    rate = {"normal": 0.055, "alerta": 0.090, "critico": 0.130}[phase]
    cyc = 2 * math.pi * rate * t
    lift = (1 - math.cos(2 * cyc)) / 2
    yaw = 0.62 * math.cos(cyc)
    shoulder = 0.55 + 0.45 * lift
    elbow = -1.28 + 0.78 * lift
    wrist = -(shoulder + elbow) * 0.55 + 0.30
    grip = 0.028 * (0.5 + 0.5 * math.sin(cyc - math.pi / 2)) ** 1.2
    return yaw, shoulder, elbow, wrist, grip


def disturbance(t: float, phase: str):
    """(torque_cotovelo_N·m, torque_queda_ombro_N·m) injetado por fase."""
    if phase == "normal":
        return 0.0, 0.0
    if phase == "alerta":
        return 22.0 * math.sin(2 * math.pi * 6.0 * t), 4.0
    return 30.0 * math.sin(2 * math.pi * 10.0 * t), 8.0


LAMP = {
    "normal": ("lamp_g_on", "lamp_a_off", "lamp_r_off"),
    "alerta": ("lamp_g_off", "lamp_a_on", "lamp_r_off"),
    "critico": ("lamp_g_off", "lamp_a_off", "lamp_r_on"),
}


class Sim:
    def __init__(self):
        ASSETS_ARM = str((Path(__file__).parent / "assets" / "arm").as_posix())
        self.model = mujoco.MjModel.from_xml_string(MJCF.replace("__MESHDIR__", ASSETS_ARM))
        self.data = mujoco.MjData(self.model)
        self.renderer = mujoco.Renderer(self.model, H, W)
        self.jid = {}
        self.qadr = {}
        self.dofadr = {}
        for name in ("j_yaw", "j_shoulder", "j_elbow", "j_wrist"):
            jid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            self.jid[name] = jid
            self.qadr[name] = self.model.jnt_qposadr[jid]
            self.dofadr[name] = self.model.jnt_dofadr[jid]
        self.lamp_geoms = {
            "green": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "lamp_green"),
            "amber": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "lamp_amber"),
            "red": mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "lamp_red"),
        }
        self.lamp_mats = {
            n: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_MATERIAL, n)
            for n in ("lamp_g_on", "lamp_g_off", "lamp_a_on", "lamp_a_off", "lamp_r_on", "lamp_r_off")
        }
        self._lamp_phase = None

        yaw, shoulder, elbow, wrist, _ = cycle_targets(0.0, "normal")
        for name, v in (("j_yaw", yaw), ("j_shoulder", shoulder), ("j_elbow", elbow), ("j_wrist", wrist)):
            self.data.qpos[self.qadr[name]] = v
        for _ in range(600):
            self._step(0.0, "normal")

    def _apply(self, t: float, phase: str):
        yaw, shoulder, elbow, wrist, _grip = cycle_targets(t, phase)
        self.data.ctrl[:] = np.array([yaw, shoulder, elbow, wrist])
        d_elbow, sag = disturbance(t, phase)
        self.data.qfrc_applied[:] = 0.0
        if d_elbow:
            self.data.qfrc_applied[self.dofadr["j_elbow"]] = d_elbow
            self.data.qfrc_applied[self.dofadr["j_wrist"]] = d_elbow * 0.35
        if sag:
            self.data.qfrc_applied[self.dofadr["j_shoulder"]] = sag

    def _step(self, t: float, phase: str):
        self._apply(t, phase)
        mujoco.mj_step(self.model, self.data)

    def set_lamp(self, phase: str):
        if phase == self._lamp_phase:
            return
        g_on, a_on, r_on = LAMP[phase]
        self.model.geom_matid[self.lamp_geoms["green"]] = self.lamp_mats[g_on]
        self.model.geom_matid[self.lamp_geoms["amber"]] = self.lamp_mats[a_on]
        self.model.geom_matid[self.lamp_geoms["red"]] = self.lamp_mats[r_on]
        self._lamp_phase = phase

    def render_frame(self, frame_idx: int, total_frames: int, t: float):
        phase = phase_of(frame_idx, total_frames)
        steps = max(1, round((1.0 / 30.0) / 0.002))
        for _ in range(steps):
            self._step(t, phase)
        self.set_lamp(phase)
        self.renderer.update_scene(self.data, camera="overview")
        img = self.renderer.render()
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR), phase


def hud_overlay(img: np.ndarray, t: float, phase: str, temp: float, servo: float, frame_idx: int) -> np.ndarray:
    color = {"normal": (40, 180, 40), "alerta": (0, 200, 255), "critico": (0, 0, 220)}[phase]
    cv2.rectangle(img, (0, 0), (W, 6), color, -1)
    cv2.putText(img, f"TEMP {temp:.1f}C", (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 2)
    cv2.putText(img, f"SERVO {servo:.0f}", (12, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
    cv2.putText(img, "3D-SIM", (W - 96, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (160, 230, 255), 1)
    cv2.putText(img, phase.upper(), (W - 128, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    if phase == "critico":
        cv2.putText(img, "!! E-STOP", (W - 150, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 60, 255), 2)
        cv2.rectangle(img, (0, H - 8), (W, H - 4), (0, 0, 180), -1)
    now_str = datetime.now().strftime("%H:%M:%S")
    cv2.putText(img, f"{now_str}  f={frame_idx}", (12, H - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (170, 170, 170), 1)
    return img


def generate(output: Path, duration: float, fps: int = 30) -> None:
    print("Construindo célula robótica (MuJoCo)…")
    sim = Sim()
    total_frames = int(duration * fps)
    dt = 1.0 / fps

    tmp_out = output.with_suffix(".part.mp4")
    tmp_side = tmp_out.with_suffix(".telemetry.jsonl")
    writer = cv2.VideoWriter(str(tmp_out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    sidecar = open(tmp_side, "w", encoding="utf-8")

    t = 0.0
    t0 = time.time()
    min_mean = 255.0
    for frame_idx in range(total_frames):
        img, phase = sim.render_frame(frame_idx, total_frames, t)
        min_mean = min(min_mean, float(img.mean()))
        temp = temperature_at(t, phase)
        servo = round(rpm_at(t, phase), 1)
        img = hud_overlay(img, t, phase, temp, servo, frame_idx)
        writer.write(img)
        sidecar.write(json.dumps({
            "frame": frame_idx,
            "seconds": round(t, 3),
            "phase": phase,
            "temperature": round(temp, 2),
            "rpm": servo,
        }) + "\n")
        t += dt

    writer.release()
    sidecar.close()
    dt_total = time.time() - t0
    print(f"Brilho mínimo dos frames: {min_mean:.2f}")
    if min_mean < 2.0:
        print("ERRO: frames pretos detectados — vídeo descartado, arquivo atual preservado.")
        tmp_out.unlink(missing_ok=True)
        tmp_side.unlink(missing_ok=True)
        raise SystemExit(1)
    os.replace(tmp_out, output)
    os.replace(tmp_side, output.with_suffix(".telemetry.jsonl"))
    print(f"Vídeo 3D: {output}  ({total_frames} frames em {dt_total:.1f}s)")
    print(f"Sidecar:  {output.with_suffix('.telemetry.jsonl')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera vídeo de simulação física 3D (braço robótico) para o EVO-VISION")
    parser.add_argument("--output", default="captures/simulation_3d.mp4")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    generate(out, duration=args.duration, fps=args.fps)
