/* ============================================================
   WASDW — Particle Background Animation
   Mouse/touch interactive — monochrome (black & white) palette
   ============================================================ */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {

        /* ── Canvas setup ── */
        var canvas = document.createElement('canvas');
        canvas.id = 'animationCanvas';
        Object.assign(canvas.style, {
            position: 'fixed',
            top:      '0',
            left:     '0',
            width:    '100%',
            height:   '100%',
            zIndex:   '-1',
            opacity:  '0.75',
            pointerEvents: 'none'
        });
        document.body.prepend(canvas);

        var ctx = canvas.getContext('2d');

        function resizeCanvas() {
            canvas.width  = window.innerWidth;
            canvas.height = window.innerHeight;
        }
        window.addEventListener('resize', resizeCanvas);
        resizeCanvas();

        /* ── Mouse / touch tracking ── */
        var mouse = { x: null, y: null, radius: 150 };

        window.addEventListener('mousemove', function (event) {
            mouse.x = event.clientX;
            mouse.y = event.clientY;
        });
        window.addEventListener('touchmove', function (event) {
            if (event.touches.length > 0) {
                mouse.x = event.touches[0].clientX;
                mouse.y = event.touches[0].clientY;
            }
        }, { passive: true });
        window.addEventListener('mouseout', function () {
            mouse.x = null;
            mouse.y = null;
        });

        /* ── Monochrome palette ── */
        var COLORS = [
            '#ffffff',  /* pure white      */
            '#e8eaf0',  /* off-white       */
            '#c0c4d0',  /* light grey      */
            '#909aaa',  /* mid grey        */
            '#606878',  /* dark grey       */
            '#404550'   /* near-black grey */
        ];

        /* ── Config ── */
        var PARTICLE_COUNT    = 100;
        var PARTICLE_BASE     = 3;
        var PARTICLE_VARY     = 1.5;
        var BASE_SPEED        = 0.2;
        var CONNECTION_DIST   = 150;
        var CONNECTION_WIDTH  = 0.8;

        var particles = [];

        /* ── Particle class ── */
        function Particle() {
            this.x          = Math.random() * canvas.width;
            this.y          = Math.random() * canvas.height;
            this.vx         = (Math.random() - 0.5) * BASE_SPEED;
            this.vy         = (Math.random() - 0.5) * BASE_SPEED;
            this.size       = Math.random() * PARTICLE_VARY + PARTICLE_BASE;
            this.origSize   = this.size;
            this.color      = COLORS[Math.floor(Math.random() * COLORS.length)];
            this.opacity    = Math.random() * 0.5 + 0.4;
            this.pulseSpeed = 0.01 + Math.random() * 0.02;
            this.pulseOffset= Math.random() * Math.PI * 2;
            this.pulseSize  = 0;
        }

        Particle.prototype.interactWithMouse = function () {
            if (mouse.x !== null && mouse.y !== null) {
                var dx = this.x - mouse.x;
                var dy = this.y - mouse.y;
                var distance = Math.sqrt(dx * dx + dy * dy);

                if (distance < mouse.radius && distance > 0) {
                    var forceDirectionX = dx / distance;
                    var forceDirectionY = dy / distance;
                    var force = (mouse.radius - distance) / mouse.radius;

                    this.vx  += forceDirectionX * force * 0.6;
                    this.vy  += forceDirectionY * force * 0.6;
                    this.size = this.origSize * (1 + force * 1.5);
                } else {
                    this.size = this.origSize;
                }
            }
        };

        Particle.prototype.update = function () {
            /* velocity cap */
            this.vx = Math.max(Math.min(this.vx, 2), -2);
            this.vy = Math.max(Math.min(this.vy, 2), -2);

            this.x += this.vx;
            this.y += this.vy;

            /* wrap edges */
            if (this.x < 0)             this.x = canvas.width;
            if (this.x > canvas.width)  this.x = 0;
            if (this.y < 0)             this.y = canvas.height;
            if (this.y > canvas.height) this.y = 0;

            /* friction */
            this.vx *= 0.99;
            this.vy *= 0.99;

            this.interactWithMouse();

            /* pulse */
            this.pulseSize = (Math.sin(Date.now() * this.pulseSpeed + this.pulseOffset) + 1) * 0.5;
        };

        Particle.prototype.draw = function () {
            var currentSize = this.size * (1 + this.pulseSize * 0.3);

            /* outer glow */
            ctx.beginPath();
            ctx.arc(this.x, this.y, currentSize + 2, 0, Math.PI * 2);
            ctx.fillStyle  = this.color;
            ctx.globalAlpha = this.opacity * 0.2;
            ctx.fill();

            /* core dot */
            ctx.beginPath();
            ctx.arc(this.x, this.y, currentSize, 0, Math.PI * 2);
            ctx.fillStyle  = this.color;
            ctx.globalAlpha = this.opacity;
            ctx.fill();

            /* highlight */
            ctx.beginPath();
            ctx.arc(
                this.x - currentSize * 0.3,
                this.y - currentSize * 0.3,
                currentSize * 0.4,
                0, Math.PI * 2
            );
            ctx.fillStyle  = '#ffffff';
            ctx.globalAlpha = this.opacity * 0.4;
            ctx.fill();

            ctx.globalAlpha = 1;
        };

        /* ── Init particles ── */
        for (var i = 0; i < PARTICLE_COUNT; i++) {
            particles.push(new Particle());
        }

        /* ── Connection lines ── */
        function drawConnections() {
            ctx.lineWidth = CONNECTION_WIDTH;

            for (var a = 0; a < particles.length; a++) {
                for (var b = a + 1; b < particles.length; b++) {
                    var dx = particles[a].x - particles[b].x;
                    var dy = particles[a].y - particles[b].y;
                    var dist = Math.sqrt(dx * dx + dy * dy);

                    if (dist < CONNECTION_DIST) {
                        var opacity = 1 - (dist / CONNECTION_DIST);

                        var gradient = ctx.createLinearGradient(
                            particles[a].x, particles[a].y,
                            particles[b].x, particles[b].y
                        );
                        gradient.addColorStop(0, particles[a].color);
                        gradient.addColorStop(1, particles[b].color);

                        ctx.beginPath();
                        ctx.strokeStyle = gradient;
                        ctx.globalAlpha = opacity * 0.5;
                        ctx.moveTo(particles[a].x, particles[a].y);
                        ctx.lineTo(particles[b].x, particles[b].y);
                        ctx.stroke();
                        ctx.globalAlpha = 1;
                    }
                }
            }
        }

        /* ── Animation loop ── */
        function animate() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);

            for (var i = 0; i < particles.length; i++) {
                particles[i].update();
            }

            drawConnections();

            for (var i = 0; i < particles.length; i++) {
                particles[i].draw();
            }

            requestAnimationFrame(animate);
        }

        animate();
    });
})();
