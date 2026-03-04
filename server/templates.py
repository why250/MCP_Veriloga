"""
VerilogA code template library.

Each template is a complete, ready-to-use VerilogA module with inline
comments explaining the key constructs.  Templates are returned by the
get_veriloga_template() MCP tool.
"""

from __future__ import annotations

from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

TEMPLATES: Dict[str, Dict[str, str]] = {

    # -----------------------------------------------------------------------
    "resistor": {
        "title": "Linear Resistor",
        "description": "Simple two-terminal linear resistor with parameter R.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Linear resistor: V = I * R
module resistor(p, n);
    inout p, n;
    electrical p, n;

    parameter real R = 1e3 from (0:inf);  // Resistance in Ohms

    analog begin
        V(p, n) <+ R * I(p, n);
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "capacitor": {
        "title": "Linear Capacitor",
        "description": "Two-terminal linear capacitor: I = C * dV/dt.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Linear capacitor: I = C * dV/dt
module capacitor(p, n);
    inout p, n;
    electrical p, n;

    parameter real C = 1e-12 from (0:inf);  // Capacitance in Farads

    analog begin
        I(p, n) <+ C * ddt(V(p, n));
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "inductor": {
        "title": "Linear Inductor",
        "description": "Two-terminal linear inductor: V = L * dI/dt.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Linear inductor: V = L * dI/dt
module inductor(p, n);
    inout p, n;
    electrical p, n;

    parameter real L = 1e-9 from (0:inf);  // Inductance in Henries

    analog begin
        V(p, n) <+ L * ddt(I(p, n));
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "diode": {
        "title": "Ideal Diode (Shockley)",
        "description": "Shockley diode equation: Id = Is * (exp(Vd/n*Vt) - 1).",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Ideal Shockley diode
module diode(anode, cathode);
    inout anode, cathode;
    electrical anode, cathode;

    parameter real Is  = 1e-14 from (0:inf);  // Saturation current (A)
    parameter real N   = 1.0   from (0:inf);  // Ideality factor
    parameter real Tnom = 27;                  // Nominal temperature (°C)

    real Vt;   // Thermal voltage

    analog begin
        Vt = `P_K * ($temperature + `P_CELSIUS0) / `P_Q;
        I(anode, cathode) <+ Is * (limexp(V(anode, cathode) / (N * Vt)) - 1);
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "vccs": {
        "title": "Voltage-Controlled Current Source (VCCS)",
        "description": "VCCS: I_out = Gm * V_in.  Useful for transconductance amplifier models.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Voltage-Controlled Current Source (VCCS)
// I(out+, out-) = Gm * V(in+, in-)
module vccs(inp, inn, outp, outn);
    input  inp, inn;
    output outp, outn;
    electrical inp, inn, outp, outn;

    parameter real Gm = 1e-3;  // Transconductance (A/V)

    analog begin
        I(outp, outn) <+ Gm * V(inp, inn);
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "vcvs": {
        "title": "Voltage-Controlled Voltage Source (VCVS)",
        "description": "VCVS: V_out = A * V_in.  Ideal voltage amplifier.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Voltage-Controlled Voltage Source (VCVS)
// V(out+, out-) = A * V(in+, in-)
module vcvs(inp, inn, outp, outn);
    input  inp, inn;
    output outp, outn;
    electrical inp, inn, outp, outn;

    parameter real A = 1.0;  // Voltage gain (V/V)

    analog begin
        V(outp, outn) <+ A * V(inp, inn);
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "nmos_simple": {
        "title": "Simple NMOS (Level-1 Long-Channel)",
        "description": "Level-1 MOSFET model with threshold, linear and saturation regions.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Simple Level-1 NMOS transistor
module nmos_simple(drain, gate, source, bulk);
    inout drain, gate, source, bulk;
    electrical drain, gate, source, bulk;

    parameter real Vth0 = 0.5    from (-inf:inf);  // Threshold voltage (V)
    parameter real kp   = 200e-6 from (0:inf);     // Process transconductance (A/V^2)
    parameter real W    = 10e-6  from (0:inf);     // Channel width (m)
    parameter real L    = 1e-6   from (0:inf);     // Channel length (m)
    parameter real lambda = 0.1  from [0:inf);     // Channel-length modulation (1/V)

    real Vgs, Vds, Vbs, Vth, Id;
    real WL;

    analog begin
        WL  = W / L;
        Vgs = V(gate, source);
        Vds = V(drain, source);
        Vbs = V(bulk, source);
        Vth = Vth0;  // Body effect can be added here

        if (Vgs < Vth) begin
            // Cut-off
            Id = 0;
        end else if (Vds < (Vgs - Vth)) begin
            // Linear (triode)
            Id = kp * WL * ((Vgs - Vth) * Vds - 0.5 * Vds * Vds) * (1 + lambda * Vds);
        end else begin
            // Saturation
            Id = 0.5 * kp * WL * (Vgs - Vth) * (Vgs - Vth) * (1 + lambda * Vds);
        end

        I(drain, source) <+ Id;
        I(bulk, source)  <+ 0;
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "opamp_ideal": {
        "title": "Ideal Op-Amp",
        "description": "Ideal op-amp with finite open-loop gain and dominant pole.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Ideal single-pole op-amp
// Vout = A0 / (1 + s/wp) * Vdiff
module opamp_ideal(inp, inn, out);
    input  inp, inn;
    output out;
    electrical inp, inn, out;

    parameter real A0   = 1e5  from (1:inf);   // DC open-loop gain (V/V)
    parameter real fp   = 10.0 from (0:inf);   // Dominant pole frequency (Hz)
    parameter real Vcc  =  5.0 from (0:inf);   // Positive supply (V)
    parameter real Vss  = -5.0 from (-inf:0];  // Negative supply (V)

    real Vdiff, wp, Vout_lin;

    analog begin
        Vdiff = V(inp, inn);
        wp    = 2 * `M_PI * fp;

        // First-order low-pass via idt (integrator trick)
        // d(Vout)/dt + wp * Vout = wp * A0 * Vdiff
        V(out) <+ idt(wp * (A0 * Vdiff - V(out)), 0) + 0;

        // Output voltage clamp
        V(out) <+ max(Vss, min(Vcc, V(out)));
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "vco": {
        "title": "Voltage-Controlled Oscillator (VCO)",
        "description": "Simple VCO: output frequency = f0 + Kvco * Vctrl.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Simple behavioral VCO
// fout = f0 + Kvco * Vctrl
module vco(ctrl, out);
    input  ctrl;
    output out;
    electrical ctrl, out;

    parameter real f0   = 1e9  from (0:inf);  // Free-running frequency (Hz)
    parameter real Kvco = 1e8  from (0:inf);  // VCO gain (Hz/V)
    parameter real Vamp = 1.0  from (0:inf);  // Output amplitude (V)

    real freq, phase;

    analog begin
        freq  = f0 + Kvco * V(ctrl);
        phase = 2 * `M_PI * idtmod(freq, 0.0, 1.0, 0.0);
        V(out) <+ Vamp * sin(phase);
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "transmission_line": {
        "title": "Lossless Transmission Line",
        "description": "Behavioral lossless transmission line using laplace_nd or delay.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Lossless transmission line (behavioral)
// Terminated with characteristic impedance Z0; delay = TD seconds
module tline(in_p, in_n, out_p, out_n);
    input  in_p, in_n;
    output out_p, out_n;
    electrical in_p, in_n, out_p, out_n;

    parameter real Z0 = 50.0  from (0:inf);  // Characteristic impedance (Ohm)
    parameter real TD = 1e-9  from (0:inf);  // Propagation delay (s)

    analog begin
        // Output port: delayed copy of input
        V(out_p, out_n) <+ absdelay(V(in_p, in_n), TD);
        // Input port: source termination with Z0
        I(in_p, in_n) <+ V(in_p, in_n) / Z0;
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "noise_source": {
        "title": "Thermal Noise Current Source",
        "description": "White thermal noise source: S_I = 4*k*T/R.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Thermal noise current source (Johnson-Nyquist noise)
// Noise spectral density: S_I = 4 * k * T / R
module noise_resistor(p, n);
    inout p, n;
    electrical p, n;

    parameter real R = 1e3 from (0:inf);  // Resistance (Ohm)

    analog begin
        // Deterministic (DC) contribution
        V(p, n) <+ R * I(p, n);

        // Thermal noise — white spectrum
        I(p, n) <+ white_noise(4 * `P_K * $temperature / R, "thermal");
    end
endmodule
""",
    },

    # -----------------------------------------------------------------------
    "pll_phase_detector": {
        "title": "Phase-Frequency Detector (PFD)",
        "description": "Behavioral PFD outputting an analog phase-error signal.",
        "code": """\
`include "disciplines.vams"
`include "constants.vams"

// Behavioral Phase-Frequency Detector (PFD)
// Outputs voltage proportional to phase difference between ref and div.
module pfd(ref, div, up, dn);
    input  ref, div;
    output up, dn;
    electrical ref, div, up, dn;

    parameter real Vhigh = 1.8 from (0:inf);  // Logic high level (V)
    parameter real Vlow  = 0.0;               // Logic low level (V)
    parameter real Trise = 10e-12 from (0:inf);  // Rise/fall time (s)

    real up_state, dn_state;

    analog begin
        @(cross(V(ref) - Vhigh / 2, +1)) up_state = 1;
        @(cross(V(div) - Vhigh / 2, +1)) dn_state = 1;

        if (up_state && dn_state) begin
            up_state = 0;
            dn_state = 0;
        end

        V(up) <+ transition(up_state * Vhigh, 0, Trise);
        V(dn) <+ transition(dn_state * Vhigh, 0, Trise);
    end
endmodule
""",
    },
}

# Aliases to ease lookup (case-insensitive, with common synonyms)
_ALIASES: Dict[str, str] = {
    "res": "resistor",
    "r": "resistor",
    "cap": "capacitor",
    "c": "capacitor",
    "ind": "inductor",
    "l": "inductor",
    "diode": "diode",
    "d": "diode",
    "vccs": "vccs",
    "gm": "vccs",
    "vcvs": "vcvs",
    "gain": "vcvs",
    "nmos": "nmos_simple",
    "mosfet": "nmos_simple",
    "mos": "nmos_simple",
    "fet": "nmos_simple",
    "transistor": "nmos_simple",
    "opamp": "opamp_ideal",
    "op_amp": "opamp_ideal",
    "amplifier": "opamp_ideal",
    "vco": "vco",
    "oscillator": "vco",
    "tline": "transmission_line",
    "txline": "transmission_line",
    "transmission": "transmission_line",
    "coax": "transmission_line",
    "noise": "noise_source",
    "noisy_resistor": "noise_source",
    "pfd": "pll_phase_detector",
    "phase_detector": "pll_phase_detector",
    "pll": "pll_phase_detector",
}


def get_template(model_type: str) -> Optional[Dict[str, str]]:
    """
    Look up a template by model_type (case-insensitive).
    Returns dict with keys: title, description, code — or None if not found.
    """
    key = model_type.lower().strip().replace(" ", "_").replace("-", "_")
    key = _ALIASES.get(key, key)
    return TEMPLATES.get(key)


def list_templates() -> str:
    """Return a formatted list of available template names."""
    lines = ["Available VerilogA model templates:\n"]
    for key, val in TEMPLATES.items():
        lines.append(f"  {key:<25}  {val['description']}")
    lines.append("\nAliases: " + ", ".join(sorted(_ALIASES)))
    return "\n".join(lines)
