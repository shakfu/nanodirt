// MP3 decoding for nanodirt's soundfonts: minimp3 (thirdparty/minimp3, CC0) on a bytes object.
// Copyright (C) 2026 nanodirt contributors. GPL-3.0-or-later.
//
// Py_LIMITED_API, when applicable, is supplied by setup.py.
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define MINIMP3_IMPLEMENTATION
#define MINIMP3_FLOAT_OUTPUT
#define MINIMP3_NO_STDIO
#include "minimp3_ex.h"

PyDoc_STRVAR(decode_doc,
             "decode(data: bytes) -> tuple[bytes, int, int]\n\n"
             "Interleaved native-endian float32 samples, channels and sample rate.\n"
             "Encoder delay and padding recorded in a LAME or Xing tag are removed.\n"
             "Raises ValueError if *data* holds no MP3 frames.");

static PyObject *decode(PyObject *self, PyObject *args) {
	const char *data;
	Py_ssize_t size;
	if (!PyArg_ParseTuple(args, "y#:decode", &data, &size)) {
		return NULL;
	}
	mp3dec_t *dec = malloc(sizeof(mp3dec_t));
	if (dec == NULL) {
		return PyErr_NoMemory();
	}
	mp3dec_file_info_t info;
	memset(&info, 0, sizeof(info));  // left unset when minimp3 rejects its arguments
	int ret;
	// The bytes object is immutable and held by args while the GIL is released.
	Py_BEGIN_ALLOW_THREADS
	ret = mp3dec_load_buf(dec, (const uint8_t *)data, (size_t)size, &info, NULL, NULL);
	Py_END_ALLOW_THREADS
	free(dec);
	PyObject *result = NULL;
	if (ret == MP3D_E_MEMORY) {
		PyErr_NoMemory();
	} else if (ret < 0) {
		PyErr_Format(PyExc_ValueError, "cannot decode MP3 (minimp3 error %d)", ret);
	} else if (info.samples == 0 || info.channels < 1 || info.hz < 1) {
		PyErr_SetString(PyExc_ValueError, "no MP3 frames in data");
	} else if (info.samples > (size_t)(PY_SSIZE_T_MAX / sizeof(mp3d_sample_t))) {
		PyErr_NoMemory();
	} else {
		PyObject *pcm = PyBytes_FromStringAndSize(
		    (const char *)info.buffer, (Py_ssize_t)(info.samples * sizeof(mp3d_sample_t)));
		if (pcm != NULL) {
			result = Py_BuildValue("(Nii)", pcm, info.channels, info.hz);
		}
	}
	free(info.buffer);  // set by mp3dec_load_buf, also on some errors; free(NULL) is a no-op
	return result;
}

static PyMethodDef methods[] = {
    {"decode", decode, METH_VARARGS, decode_doc},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT, "_mp3", "MP3 decoding (minimp3).", -1, methods,
};

PyMODINIT_FUNC PyInit__mp3(void) { return PyModule_Create(&moduledef); }
