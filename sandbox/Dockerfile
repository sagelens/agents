# Use a small official Python runtime with broad wheel compatibility.
FROM python:3.12-slim

# Prevent Python from buffering captured output.
ENV PYTHONUNBUFFERED=1
# Force matplotlib to render files without a display server.
ENV MPLBACKEND=Agg
# Keep matplotlib's writable cache on the runtime tmpfs.
ENV MPLCONFIGDIR=/tmp/matplotlib
# Keep numerical libraries within the runtime's CPU and process budget.
ENV OPENBLAS_NUM_THREADS=1
# Keep OpenMP-backed libraries single-threaded.
ENV OMP_NUM_THREADS=1
# Keep MKL-backed libraries single-threaded when present.
ENV MKL_NUM_THREADS=1

# Copy the pinned sandbox-only dependency manifest.
COPY requirements.txt /tmp/requirements.txt
# Install scientific packages into the immutable image.
RUN pip install --no-cache-dir --requirement /tmp/requirements.txt
# Create an unprivileged runtime identity.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin sandbox
# Use a neutral working directory on the read-only container filesystem.
WORKDIR /work
# Drop root privileges before any generated code runs.
USER 10001:10001

# Accept Python source through standard input by default.
CMD ["python", "-"]
