from manim import *

SICK = 44
FALSE_POS = [7, 23, 58, 71, 96]
HEALTHY = "#94a3b8"
SICK_C = "#f43f5e"


def grid():
    dots = VGroup(*[Dot(radius=0.16, color=HEALTHY) for _ in range(100)])
    dots.arrange_in_grid(rows=10, cols=10, buff=0.3).move_to(UP * 0.5)
    return dots


def ring(d):
    return Circle(radius=0.27, color=PV_ACCENT, stroke_width=6).move_to(d)


class People(Scene):
    def construct(self):
        dots = grid()
        self.play(LaggedStart(*[FadeIn(d, scale=0.5) for d in dots], lag_ratio=0.02), run_time=2)
        label = Text("100人", font_size=44).next_to(dots, DOWN, buff=0.45)
        self.play(FadeIn(label), run_time=0.5)
        self.wait(1.2)
        sick = dots[SICK]
        self.play(sick.animate.set_color(SICK_C).scale(1.3), run_time=0.8)
        self.play(Create(ring(sick)), run_time=0.8)
        self.wait(2.5)
        self.play(LaggedStart(*[Create(ring(dots[i])) for i in FALSE_POS], lag_ratio=0.3), run_time=2)
        self.wait(1)


class Result(Scene):
    def construct(self):
        dots = grid()
        dots[SICK].set_color(SICK_C).scale(1.3)
        rings = VGroup(*[ring(dots[i]) for i in [SICK] + FALSE_POS])
        self.add(dots, rings)
        others = VGroup(*[d for i, d in enumerate(dots) if i not in [SICK] + FALSE_POS])
        self.play(others.animate.set_opacity(0.12), run_time=1)
        pos = VGroup(*[VGroup(dots[i], r) for i, r in zip([SICK] + FALSE_POS, rings)])
        self.play(pos.animate.arrange(RIGHT, buff=0.35).move_to(UP * 0.8), FadeOut(others), run_time=1.5)
        brace = Brace(pos, DOWN)
        t1 = Text("陽性 6人", font_size=44).next_to(brace, DOWN)
        self.play(GrowFromCenter(brace), FadeIn(t1), run_time=0.8)
        self.wait(1)
        frac = MathTex(r"\frac{1}{6}", r"\approx", r"17\%", font_size=110).next_to(t1, DOWN, buff=0.6)
        frac[0].set_color(SICK_C)
        frac[2].set_color(PV_ACCENT)
        self.play(Write(frac), run_time=1.5)
        self.wait(1)


class Formula(Scene):
    def construct(self):
        legend = Text("A = 病気　　B = 陽性", font_size=40).to_edge(UP, buff=0.6)
        left = MathTex(r"P(A\mid B)", font_size=72)
        eq = MathTex("=", font_size=72)
        like = MathTex(r"P(B\mid A)", font_size=72)
        prior = MathTex(r"P(A)", font_size=72)
        num = VGroup(like, prior).arrange(RIGHT, buff=0.2)
        den = MathTex(r"P(B)", font_size=72)
        bar = Line(LEFT, RIGHT).set_width(num.width + 0.2)
        frac = VGroup(num, bar, den).arrange(DOWN, buff=0.18)
        formula = VGroup(left, eq, frac).arrange(RIGHT, buff=0.3).move_to(DOWN * 0.2)
        formula.scale_to_fit_width(min(formula.width, config.frame_width - 1.8))
        self.play(FadeIn(legend), run_time=0.6)
        self.play(Write(formula), run_time=2)
        self.wait(0.8)
        b1 = SurroundingRectangle(left, color=PV_ACCENT, buff=0.12)
        t1 = Text("事後確率", font_size=40, color=PV_ACCENT).next_to(b1, DOWN, buff=0.25)
        self.play(Create(b1), FadeIn(t1), run_time=0.8)
        self.wait(2.2)
        b2 = SurroundingRectangle(prior, color="#f43f5e", buff=0.12)
        t2 = Text("事前確率", font_size=40, color="#f43f5e").next_to(frac, DOWN, buff=0.35).align_to(b2, RIGHT)
        self.play(Create(b2), FadeIn(t2), run_time=0.8)
        self.wait(1.5)
        self.play(Indicate(prior, color="#f43f5e", scale_factor=1.3), Indicate(left, color=PV_ACCENT, scale_factor=1.15), run_time=1.5)
        self.wait(1)
