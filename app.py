import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from flask import Flask, render_template, request
from matplotlib.collections import LineCollection

import sqlite3
from flask import session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

import fastf1 as ff1
from fastf1 import plotting
from datetime import timedelta


# ===================== APP SETUP =====================

app = Flask(__name__)
app.secret_key = "f1_secret_key"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
PLOT_DIR = os.path.join(BASE_DIR, "static", "plot")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(PLOT_DIR, exist_ok=True)

ff1.Cache.enable_cache(CACHE_DIR)

plotting.setup_mpl(
    mpl_timedelta_support=True,
    color_scheme='fastf1'
)
def init_db():
    conn = sqlite3.connect("f1_analysis.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT DEFAULT 'user'
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analysis_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        year TEXT,
        gp TEXT,
        session TEXT,
        drivers TEXT,
        analysis_type TEXT,
        description TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()


# ===================== ROUTE =====================
@app.route("/auth", methods=["GET", "POST"])
def auth():

    if "user" in session:
        return redirect(url_for("index"))

    if request.method == "POST":

        action = request.form.get("action")  # login or register
        username = request.form.get("username")
        password = request.form.get("password")
        remember = request.form.get("remember")

        if not username or not password:
            return render_template("auth.html", error="All fields required")

        conn = sqlite3.connect("f1_analysis.db")
        cursor = conn.cursor()

        if action == "register":
            try:
                hashed_password = generate_password_hash(password)

                cursor.execute(
                    "INSERT INTO users (username, password) VALUES (?, ?)",
                    (username, hashed_password)
                )
                conn.commit()
                conn.close()

                return render_template("auth.html", success="Registered successfully. Please login.")

            except sqlite3.IntegrityError:
                conn.close()
                return render_template("auth.html", error="Username already exists")

        elif action == "login":
            cursor.execute("SELECT password FROM users WHERE username = ?", (username,))
            user = cursor.fetchone()
            conn.close()

            if user and check_password_hash(user[0], password):
                session["user"] = username

                if remember:
                    session.permanent = True
                    app.permanent_session_lifetime = timedelta(days=7)
                else:
                    session.permanent = False

                return redirect(url_for("index"))
            else:
                return render_template("auth.html", error="Invalid Credentials")

    return render_template("auth.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth"))

@app.route("/", methods=["GET", "POST"])
def index():
    if "user" not in session:
        return redirect(url_for("auth"))

    if request.method == "POST":

        year = request.form.get("year")
        gp = request.form.get("gp")
        session_type = request.form.get("session")
        drivers_raw = request.form.get("drivers")
        analysis = request.form.get("analysis")
        description = request.form.get("description")  # <-- NEW

        try:
            # Safety check for empty drivers
            if not drivers_raw:
                raise ValueError("No drivers entered")

            # Convert driver input into list
            drivers = [d.strip().upper() for d in drivers_raw.split(",")]

            input_data = [year, gp, session_type, drivers, analysis]

            # Your existing function
            image = get_race_data(input_data)

            return render_template(
                "index.html",
                image=image,
                description=description   # <-- SEND TO HTML
            )

        except Exception as e:
            print("Form Error:", e)

            return render_template(
                "index.html",
                image=None,
                description="Error generating analysis. Please check inputs."
            )

    # For GET request
    return render_template("index.html", image=None, description=None)

# ===================== CONTROLLER =====================

def get_race_data(input_data):

    try:
        race = ff1.get_session(
            int(input_data[0]),
            input_data[1],
            input_data[2]
        )
        race.load()

        graph_type = input_data[4]

        if graph_type == "Lap Time":
            return plot_laptime(race, input_data)

        elif graph_type == "Fastest Lap":
            return plot_fastest_lap(race, input_data)

        elif graph_type == "Full Telemetry":
            return plot_full_telemetry(race, input_data)

        else:
            return None

    except Exception as e:
        print("Controller Error:", e)
        return None


# ===================== CIRCUIT DRAWER =====================

def draw_circuit_map(ax, race, driver):

    try:
        lap = race.laps.pick_drivers(driver).pick_fastest()
        if lap is None:
            return None

        pos = lap.get_pos_data()
        tel = lap.get_car_data().add_distance()

        x = pos["X"].values
        y = pos["Y"].values
        speed = tel["Speed"].values

        circuit_info = race.get_circuit_info()
        rotation = circuit_info.rotation
        theta = np.deg2rad(rotation)

        rot_matrix = np.array([
            [np.cos(theta), -np.sin(theta)],
            [np.sin(theta),  np.cos(theta)]
        ])

        xy = np.vstack([x, y])
        rotated = rot_matrix @ xy

        x_rot = rotated[0]
        y_rot = rotated[1]

        points = np.array([x_rot, y_rot]).T.reshape(-1,1,2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)

        norm = plt.Normalize(speed.min(), speed.max())
        lc = LineCollection(segments, cmap="plasma", norm=norm)
        lc.set_array(speed)
        lc.set_linewidth(4)

        ax.add_collection(lc)

        ax.scatter(x_rot[0], y_rot[0], color="white", s=80)
        ax.text(x_rot[0], y_rot[0], "START", color="white")

        ax.set_aspect("equal", adjustable="box")
        ax.axis("off")

        return lc

    except Exception as e:
        print("Circuit draw error:", e)
        return None


# ===================== LAP TIME =====================

def plot_laptime(race, input_data):

    drivers = input_data[3]
    driver_text = " vs ".join(drivers)

    fig = plt.figure(figsize=(12,10))
    gs = fig.add_gridspec(2,1, height_ratios=[1,2])

    # Circuit
    ax0 = fig.add_subplot(gs[0])
    lc = draw_circuit_map(ax0, race, drivers[0])
    if lc:
        fig.colorbar(lc, ax=ax0, label="Speed (km/h)")

    ax0.set_title(f"{race.event['EventName']} Circuit Layout")

    # Lap Time Graph
    ax = fig.add_subplot(gs[1])

    for drv in drivers:
        try:
            laps = race.laps.pick_drivers(drv)
            if laps.empty:
                continue

            color = plotting.get_driver_color(drv, race)

            ax.plot(
                laps["LapNumber"],
                laps["LapTime"],
                label=drv,
                color=color
            )

        except Exception as e:
            print(f"LapTime error for {drv}:", e)

    ax.set_xlabel("Lap Number")
    ax.set_ylabel("Lap Time")
    ax.legend()

    plt.suptitle(
        f"Lap Time Comparison\n"
        f"{driver_text}\n"
        f"{race.event.year} {race.event['EventName']} {input_data[2]}"
    )

    file_name = f"Lap_Time_{np.random.randint(100000)}.png"
    file_path = os.path.join(PLOT_DIR, file_name)

    plt.savefig(file_path, dpi=200)
    plt.close()

    return file_name


# ===================== FASTEST LAP =====================

def plot_fastest_lap(race, input_data):

    drivers = input_data[3]
    driver_text = " vs ".join(drivers)

    fig = plt.figure(figsize=(12,10))
    gs = fig.add_gridspec(2,1, height_ratios=[1,2])

    # Circuit
    ax0 = fig.add_subplot(gs[0])
    lc = draw_circuit_map(ax0, race, drivers[0])
    if lc:
        fig.colorbar(lc, ax=ax0, label="Speed (km/h)")

    ax0.set_title(f"{race.event['EventName']} Circuit Layout")

    # Speed Graph
    ax = fig.add_subplot(gs[1])

    for drv in drivers:
        try:
            fastest = race.laps.pick_drivers(drv).pick_fastest()
            if fastest is None:
                continue

            tel = fastest.get_car_data().add_distance()
            color = plotting.get_driver_color(drv, race)

            ax.plot(
                tel["Distance"],
                tel["Speed"],
                label=drv,
                color=color
            )

        except Exception as e:
            print(f"Fastest lap error for {drv}:", e)

    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("Speed (km/h)")
    ax.legend()

    plt.suptitle(
        f"Fastest Lap Comparison\n"
        f"{driver_text}\n"
        f"{race.event.year} {race.event['EventName']} {input_data[2]}"
    )

    file_name = f"Fastest_Lap_{np.random.randint(100000)}.png"
    file_path = os.path.join(PLOT_DIR, file_name)

    plt.savefig(file_path, dpi=200)
    plt.close()

    return file_name


# ===================== FULL TELEMETRY =====================

def plot_full_telemetry(race, input_data):

    drivers = input_data[3][:3]
    driver_text = " vs ".join(drivers)

    fig, ax = plt.subplots(5, sharex=True, figsize=(10,10))

    for drv in drivers:
        try:
            lap = race.laps.pick_drivers(drv).pick_fastest()
            if lap is None:
                continue

            tel = lap.get_car_data().add_distance()
            color = plotting.get_driver_color(drv, race)

            ax[0].plot(tel["Distance"], tel["Speed"], label=drv, color=color)
            ax[1].plot(tel["Distance"], tel["Throttle"], color=color)
            ax[2].plot(tel["Distance"], tel["Brake"], color=color)
            ax[3].plot(tel["Distance"], tel["RPM"], color=color)
            ax[4].plot(tel["Distance"], tel["nGear"], color=color)

        except Exception as e:
            print(f"Telemetry error for {drv}:", e)

    ax[0].legend()
    ax[0].set_ylabel("Speed")
    ax[1].set_ylabel("Throttle")
    ax[2].set_ylabel("Brake")
    ax[3].set_ylabel("RPM")
    ax[4].set_ylabel("Gear")

    plt.suptitle(
        f"Full Telemetry Comparison\n"
        f"{driver_text}\n"
        f"{race.event.year} {race.event['EventName']} {input_data[2]}"
    )

    file_name = f"Full_Telemetry_{np.random.randint(100000)}.png"
    file_path = os.path.join(PLOT_DIR, file_name)

    plt.savefig(file_path, dpi=200)
    plt.close()

    return file_name

# ===================== RUN =====================

if __name__ == "__main__":
    app.run(debug=True)