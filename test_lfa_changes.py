"""
Tests for LFA icon changes:
- create_lfahda_cluster: LFAHDA_CLUSTER message generation
- create_lfa_icon_non_camera_scc: ADRV_0x161 LFA_ICON for non-camera SCC
- carcontroller integration: sending conditions
"""
import copy
import sys
from unittest.mock import MagicMock, patch, PropertyMock
import types

# --- Mock all heavy openpilot/opendbc dependencies ---

# Create mock modules before importing the code under test
mock_params_module = MagicMock()
mock_params_class = MagicMock()
mock_params_class.return_value = mock_params_class
mock_params_class.get_int.return_value = 0
mock_params_class.get_bool.return_value = False
mock_params_class.get.return_value = b"0"
mock_params_class.get_float.return_value = 0.0
mock_params_module.Params = mock_params_class

sys.modules['openpilot'] = MagicMock()
sys.modules['openpilot.common'] = MagicMock()
sys.modules['openpilot.common.params'] = mock_params_module
sys.modules['openpilot.common.filter_simple'] = MagicMock()
sys.modules['cereal'] = MagicMock()
sys.modules['cereal.log'] = MagicMock()

# Mock opendbc.car CRC module
mock_crc = types.ModuleType('opendbc.car.crc')
mock_crc.CRC16_XMODEM = [0] * 256
sys.modules['opendbc.car.crc'] = mock_crc

# Mock opendbc.car.hyundai.values
mock_values = MagicMock()

class MockHyundaiFlags:
    CANFD = 1
    CANFD_HDA2 = 2
    CAMERA_SCC = 4
    CANFD_CAMERA_SCC = 8
    ANGLE_CONTROL = 16
    EV = 32
    HYBRID = 64
    CANFD_ALT_BUTTONS = 128
    CANFD_ALT_GEARS = 256
    CANFD_ALT_GEARS_2 = 512
    CANFD_HDA2_ALT_STEERING = 1024
    RADAR_SCC = 2048
    ENABLE_BLINKERS = 4096
    SEND_LFA = 8192
    USE_FCA = 16384
    CC_ONLY_CAR = 32768
    LEGACY = 65536
    CLUSTER_GEARS = 131072
    TCU_GEARS = 262144
    FCEV = 524288
    ALT_LIMITS = 1048576
    MANDO_RADAR = 2097152

    # Add .value for each attribute
    def __init__(self):
        for attr in dir(self):
            if not attr.startswith('_'):
                val = getattr(self, attr)
                if isinstance(val, int):
                    prop = type('Flag', (), {'value': val, '__and__': lambda s, o: s.value & o,
                                             '__rand__': lambda s, o: o & s.value,
                                             '__bool__': lambda s: bool(s.value)})()
                    # Keep as int for simplicity

mock_values.HyundaiFlags = MockHyundaiFlags
mock_values.HyundaiExtFlags = MagicMock()
mock_values.HyundaiExtFlags.CANFD_GEARS_69 = 1
mock_values.Buttons = MagicMock()
sys.modules['opendbc.car.hyundai.values'] = mock_values

# Mock other opendbc modules
sys.modules['opendbc.can'] = MagicMock()
sys.modules['opendbc.car'] = MagicMock()
sys.modules['opendbc.car.common'] = MagicMock()
sys.modules['opendbc.car.common.conversions'] = MagicMock()
sys.modules['opendbc.car.hyundai'] = types.ModuleType('opendbc.car.hyundai')
sys.modules['opendbc.car.hyundai'].__path__ = []
sys.modules['opendbc.car.interfaces'] = MagicMock()
sys.modules['opendbc.car.vehicle_model'] = MagicMock()
sys.modules['opendbc.car.disable_ecu'] = MagicMock()
sys.modules['opendbc.car.hyundai.carcontroller'] = MagicMock()
sys.modules['opendbc.car.hyundai.carstate'] = MagicMock()
sys.modules['opendbc.car.hyundai.radar_interface'] = MagicMock()
sys.modules['opendbc.car.hyundai.hyundaican'] = MagicMock()

# --- Now import the module under test ---
# We need to import hyundaicanfd directly by reading the source
import importlib.util

# Load hyundaicanfd with mocks in place
spec = importlib.util.spec_from_file_location(
    "hyundaicanfd_test",
    "/workspace/opendbc_repo/opendbc/car/hyundai/hyundaicanfd.py"
)
hyundaicanfd = importlib.util.module_from_spec(spec)

# Patch the copy module reference
import copy as real_copy
hyundaicanfd.copy = real_copy

try:
    spec.loader.exec_module(hyundaicanfd)
except Exception as e:
    print(f"Warning loading module: {e}")


# ===================================================================
# Helper: fake packer that records make_can_msg calls
# ===================================================================
class FakePacker:
    def __init__(self):
        self.msgs = []

    def make_can_msg(self, name, bus, values, rx_counter=None):
        msg = {
            "name": name,
            "bus": bus,
            "values": dict(values),
            "rx_counter": rx_counter,
        }
        self.msgs.append(msg)
        return msg


class FakeCAN:
    ECAN = 0
    ACAN = 1
    CAM = 2


# ===================================================================
# Tests for create_lfahda_cluster
# ===================================================================

class TestCreateLfahdaCluster:
    """LFAHDA_CLUSTER 메시지 생성 테스트"""

    def test_with_camera_original_copies_values(self):
        """카메라 원본이 있으면 복사하여 사용하고 아이콘 필드만 오버라이드"""
        packer = FakePacker()
        CS = MagicMock()
        CS.lfahda_cluster = {
            "COUNTER": 42,
            "LFA_OptUsmSta": 3,
            "HDA_OptUsmSta": 2,
            "HDA_CntrlModSta": 0,
            "HDA_LFA_SymSta": 0,
            "HDA_InfoPUDis": 1,
            "HDA_AutoSetSpdSta": 1,
            "HDA_AutoSetSpdVal": 100,
        }

        result = hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, long_active=True, lat_active=True)

        assert len(result) == 1
        msg = result[0]
        assert msg["name"] == "LFAHDA_CLUSTER"
        assert msg["bus"] == FakeCAN.ECAN

        # Icon fields overridden
        assert msg["values"]["HDA_CntrlModSta"] == 2  # long active
        assert msg["values"]["HDA_LFA_SymSta"] == 2   # lat active

        # Camera original fields preserved
        assert msg["values"]["LFA_OptUsmSta"] == 3
        assert msg["values"]["HDA_OptUsmSta"] == 2
        assert msg["values"]["HDA_InfoPUDis"] == 1
        assert msg["values"]["HDA_AutoSetSpdVal"] == 100

        # COUNTER removed from values, passed as rx_counter
        assert "COUNTER" not in msg["values"]
        assert msg["rx_counter"] == 42

    def test_with_camera_original_lat_inactive(self):
        """lat 비활성일 때 HDA_LFA_SymSta = 0"""
        packer = FakePacker()
        CS = MagicMock()
        CS.lfahda_cluster = {
            "COUNTER": 10,
            "LFA_OptUsmSta": 2,
            "HDA_OptUsmSta": 2,
            "HDA_CntrlModSta": 0,
            "HDA_LFA_SymSta": 2,
        }

        result = hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, long_active=False, lat_active=False)

        msg = result[0]
        assert msg["values"]["HDA_CntrlModSta"] == 0
        assert msg["values"]["HDA_LFA_SymSta"] == 0
        assert msg["values"]["LFA_OptUsmSta"] == 2  # preserved from camera

    def test_without_camera_original_sets_opt_usm(self):
        """카메라 원본 없을 때 LFA_OptUsmSta/HDA_OptUsmSta 직접 설정"""
        packer = FakePacker()
        CS = MagicMock()
        CS.lfahda_cluster = None

        result = hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, long_active=False, lat_active=True)

        assert len(result) == 1
        msg = result[0]
        assert msg["name"] == "LFAHDA_CLUSTER"

        # Fallback values set
        assert msg["values"]["LFA_OptUsmSta"] == 2
        assert msg["values"]["HDA_OptUsmSta"] == 2

        # Icon fields
        assert msg["values"]["HDA_CntrlModSta"] == 0
        assert msg["values"]["HDA_LFA_SymSta"] == 2

        # No rx_counter
        assert msg["rx_counter"] is None

    def test_without_camera_long_active_only(self):
        """카메라 원본 없이 long만 활성"""
        packer = FakePacker()
        CS = MagicMock()
        CS.lfahda_cluster = None

        result = hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, long_active=True, lat_active=False)

        msg = result[0]
        assert msg["values"]["HDA_CntrlModSta"] == 2
        assert msg["values"]["HDA_LFA_SymSta"] == 0

    def test_does_not_mutate_original(self):
        """카메라 원본 딕셔너리를 변경하지 않음"""
        packer = FakePacker()
        CS = MagicMock()
        original = {
            "COUNTER": 5,
            "LFA_OptUsmSta": 3,
            "HDA_CntrlModSta": 0,
            "HDA_LFA_SymSta": 0,
        }
        CS.lfahda_cluster = original

        hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, long_active=True, lat_active=True)

        # Original should not be modified
        assert original["COUNTER"] == 5
        assert original["HDA_CntrlModSta"] == 0
        assert original["HDA_LFA_SymSta"] == 0

    def test_always_returns_one_message(self):
        """항상 정확히 1개의 메시지 반환"""
        packer = FakePacker()

        for lfahda in [None, {"COUNTER": 1, "LFA_OptUsmSta": 2}]:
            CS = MagicMock()
            CS.lfahda_cluster = lfahda
            for long_a in [True, False]:
                for lat_a in [True, False]:
                    result = hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, long_a, lat_a)
                    assert len(result) == 1, f"Expected 1 msg, got {len(result)} (lfahda={lfahda}, long={long_a}, lat={lat_a})"


# ===================================================================
# Tests for create_lfa_icon_non_camera_scc
# ===================================================================

class TestCreateLfaIconNonCameraScc:
    """비 camera SCC 차량용 ADRV_0x161 LFA_ICON 테스트"""

    def _make_cs(self, adrv_0x161=None):
        CS = MagicMock()
        CS.adrv_0x161 = adrv_0x161
        CS.out.latEnabled = True
        return CS

    def _make_cc(self, lat_active=True):
        CC = MagicMock()
        CC.latActive = lat_active
        return CC

    def test_no_adrv_returns_empty(self):
        """ADRV_0x161이 None이면 빈 리스트 (5W 클러스터 등)"""
        packer = FakePacker()
        CS = self._make_cs(adrv_0x161=None)
        CC = self._make_cc()

        result = hyundaicanfd.create_lfa_icon_non_camera_scc(packer, CS, FakeCAN, CC)
        assert result == []

    def test_with_adrv_lat_active(self):
        """lat 활성시 LFA_ICON=2, LKA_ICON=4"""
        packer = FakePacker()
        adrv = {
            "COUNTER": 100,
            "LFA_ICON": 0,
            "LKA_ICON": 0,
            "ALERTS_1": 0,
            "ALERTS_2": 0,
            "ALERTS_3": 0,
            "ALERTS_5": 0,
            "SOUNDS_1": 5,
            "SOUNDS_2": 3,
            "SOUNDS_3": 1,
            "SOUNDS_4": 2,
            "DAW_ICON": 1,
        }
        CS = self._make_cs(adrv_0x161=adrv)
        CC = self._make_cc(lat_active=True)

        result = hyundaicanfd.create_lfa_icon_non_camera_scc(packer, CS, FakeCAN, CC)

        assert len(result) == 1
        msg = result[0]
        assert msg["name"] == "ADRV_0x161"
        assert msg["values"]["LFA_ICON"] == 2
        assert msg["values"]["LKA_ICON"] == 4
        assert msg["rx_counter"] == 100

    def test_with_adrv_lat_inactive_but_enabled(self):
        """lat 비활성이지만 enabled일 때 LFA_ICON=1, LKA_ICON=3"""
        packer = FakePacker()
        adrv = {
            "COUNTER": 50,
            "LFA_ICON": 0,
            "LKA_ICON": 0,
            "ALERTS_1": 0,
            "ALERTS_2": 0,
            "ALERTS_3": 0,
            "ALERTS_5": 0,
            "SOUNDS_1": 0,
            "SOUNDS_2": 0,
            "SOUNDS_3": 0,
            "SOUNDS_4": 0,
            "DAW_ICON": 0,
        }
        CS = self._make_cs(adrv_0x161=adrv)
        CS.out.latEnabled = True
        CC = self._make_cc(lat_active=False)

        result = hyundaicanfd.create_lfa_icon_non_camera_scc(packer, CS, FakeCAN, CC)

        msg = result[0]
        assert msg["values"]["LFA_ICON"] == 1
        assert msg["values"]["LKA_ICON"] == 3

    def test_with_adrv_lat_disabled(self):
        """lat 완전 비활성일 때 LFA_ICON=0, LKA_ICON=0"""
        packer = FakePacker()
        adrv = {
            "COUNTER": 50,
            "LFA_ICON": 2,
            "LKA_ICON": 4,
            "ALERTS_1": 0,
            "ALERTS_2": 0,
            "ALERTS_3": 0,
            "ALERTS_5": 0,
            "SOUNDS_1": 0,
            "SOUNDS_2": 0,
            "SOUNDS_3": 0,
            "SOUNDS_4": 0,
            "DAW_ICON": 0,
        }
        CS = self._make_cs(adrv_0x161=adrv)
        CS.out.latEnabled = False
        CC = self._make_cc(lat_active=False)

        result = hyundaicanfd.create_lfa_icon_non_camera_scc(packer, CS, FakeCAN, CC)

        msg = result[0]
        assert msg["values"]["LFA_ICON"] == 0
        assert msg["values"]["LKA_ICON"] == 0

    def test_alerts_suppressed(self):
        """LKAS 관련 알림이 적절히 억제됨"""
        packer = FakePacker()
        adrv = {
            "COUNTER": 10,
            "LFA_ICON": 0,
            "LKA_ICON": 0,
            "ALERTS_1": 0,
            "ALERTS_2": 5,    # should be suppressed
            "ALERTS_3": 11,   # should be suppressed
            "ALERTS_5": 3,    # should be suppressed
            "SOUNDS_1": 9,
            "SOUNDS_2": 8,
            "SOUNDS_3": 7,
            "SOUNDS_4": 6,
            "DAW_ICON": 2,
        }
        CS = self._make_cs(adrv_0x161=adrv)
        CC = self._make_cc()

        result = hyundaicanfd.create_lfa_icon_non_camera_scc(packer, CS, FakeCAN, CC)

        msg = result[0]
        assert msg["values"]["ALERTS_2"] == 0
        assert msg["values"]["DAW_ICON"] == 0
        assert msg["values"]["ALERTS_3"] == 0
        assert msg["values"]["SOUNDS_3"] == 0
        assert msg["values"]["ALERTS_5"] == 0
        # ALERTS_1 == 0 → sounds suppressed
        assert msg["values"]["SOUNDS_1"] == 0
        assert msg["values"]["SOUNDS_2"] == 0
        assert msg["values"]["SOUNDS_4"] == 0

    def test_alerts_not_suppressed_when_legitimate(self):
        """억제 대상이 아닌 알림은 유지됨"""
        packer = FakePacker()
        adrv = {
            "COUNTER": 10,
            "LFA_ICON": 0,
            "LKA_ICON": 0,
            "ALERTS_1": 3,     # non-zero → sounds NOT suppressed
            "ALERTS_2": 15,    # not in suppress list
            "ALERTS_3": 1,     # not in suppress list
            "ALERTS_5": 8,     # not in suppress list
            "SOUNDS_1": 5,
            "SOUNDS_2": 4,
            "SOUNDS_3": 3,
            "SOUNDS_4": 2,
            "DAW_ICON": 1,
        }
        CS = self._make_cs(adrv_0x161=adrv)
        CC = self._make_cc()

        result = hyundaicanfd.create_lfa_icon_non_camera_scc(packer, CS, FakeCAN, CC)

        msg = result[0]
        assert msg["values"]["ALERTS_2"] == 15  # kept
        assert msg["values"]["DAW_ICON"] == 1   # kept
        assert msg["values"]["ALERTS_3"] == 1   # kept
        assert msg["values"]["ALERTS_5"] == 8   # kept
        # ALERTS_1 != 0 → sounds NOT suppressed
        assert msg["values"]["SOUNDS_1"] == 5
        assert msg["values"]["SOUNDS_2"] == 4
        assert msg["values"]["SOUNDS_4"] == 2

    def test_does_not_mutate_original(self):
        """원본 ADRV_0x161 딕셔너리를 변경하지 않음"""
        packer = FakePacker()
        original = {
            "COUNTER": 10,
            "LFA_ICON": 0,
            "LKA_ICON": 0,
            "ALERTS_1": 0,
            "ALERTS_2": 0,
            "ALERTS_3": 0,
            "ALERTS_5": 0,
            "SOUNDS_1": 0,
            "SOUNDS_2": 0,
            "SOUNDS_3": 0,
            "SOUNDS_4": 0,
            "DAW_ICON": 0,
        }
        CS = self._make_cs(adrv_0x161=original)
        CC = self._make_cc(lat_active=True)

        hyundaicanfd.create_lfa_icon_non_camera_scc(packer, CS, FakeCAN, CC)

        assert original["LFA_ICON"] == 0
        assert original["LKA_ICON"] == 0
        assert original["COUNTER"] == 10


# ===================================================================
# Tests for carcontroller integration logic
# ===================================================================

class TestCarControllerLfaIntegration:
    """carcontroller.py의 LFA 관련 전송 조건 테스트 (로직 검증)"""

    def test_lfahda_sent_for_all_canfd(self):
        """모든 CANFD 차량에서 LFAHDA_CLUSTER 전송 (camera_scc 조건 제거됨)"""
        # Read the actual source to verify the condition
        with open("/workspace/opendbc_repo/opendbc/car/hyundai/carcontroller.py") as f:
            src = f.read()

        # Find the LFAHDA_CLUSTER block
        lines = src.split('\n')
        for i, line in enumerate(lines):
            if 'create_lfahda_cluster' in line and 'can_sends' in line:
                # Check the condition line (one line above)
                cond_line = lines[i-1].strip()
                assert 'camera_scc' not in cond_line, \
                    f"LFAHDA_CLUSTER should not be gated by camera_scc. Found: {cond_line}"
                assert 'self.frame % 5 == 0' in cond_line, \
                    f"Should be sent every 5 frames. Found: {cond_line}"
                break
        else:
            assert False, "create_lfahda_cluster call not found in carcontroller.py"

    def test_lfa_icon_non_camera_scc_condition(self):
        """비 camera SCC일 때만 create_lfa_icon_non_camera_scc 호출"""
        with open("/workspace/opendbc_repo/opendbc/car/hyundai/carcontroller.py") as f:
            src = f.read()

        assert 'create_lfa_icon_non_camera_scc' in src, \
            "create_lfa_icon_non_camera_scc should be called in carcontroller.py"

        lines = src.split('\n')
        for i, line in enumerate(lines):
            if 'create_lfa_icon_non_camera_scc' in line and 'can_sends' in line:
                cond_line = lines[i-1].strip()
                assert 'not camera_scc' in cond_line, \
                    f"Should be gated by 'not camera_scc'. Found: {cond_line}"
                break

    def test_lfahda_cluster_function_handles_none(self):
        """lfahda_cluster가 None이어도 메시지 반환 (빈 리스트 아님)"""
        packer = FakePacker()
        CS = MagicMock()
        CS.lfahda_cluster = None

        result = hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, False, False)
        assert len(result) == 1, "Should always return 1 message even when lfahda_cluster is None"

    def test_lfahda_cluster_function_handles_existing(self):
        """lfahda_cluster가 있으면 복사하여 메시지 반환"""
        packer = FakePacker()
        CS = MagicMock()
        CS.lfahda_cluster = {"COUNTER": 5, "LFA_OptUsmSta": 2, "HDA_OptUsmSta": 1}

        result = hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, True, True)
        assert len(result) == 1
        msg = result[0]
        assert msg["values"]["LFA_OptUsmSta"] == 2
        assert msg["values"]["HDA_LFA_SymSta"] == 2


# ===================================================================
# Test for 5W cluster specific scenario
# ===================================================================

class TestGV70_5W_Scenario:
    """2021 GV70 HDA1 CANFD 5W 클러스터 시나리오"""

    def test_5w_lfahda_with_camera_data(self):
        """5W: 카메라 원본 LFAHDA_CLUSTER로부터 LFA_OptUsmSta 상속"""
        packer = FakePacker()
        CS = MagicMock()
        # Camera sends LFAHDA_CLUSTER with LFA enabled
        CS.lfahda_cluster = {
            "COUNTER": 77,
            "LFA_OptUsmSta": 2,   # camera says LFA is available
            "HDA_OptUsmSta": 1,
            "HDA_CntrlModSta": 0,
            "HDA_LFA_SymSta": 0,  # camera says LFA inactive
            "HDA_InfoPUDis": 0,
            "HDA_AutoSetSpdSta": 0,
            "HDA_AutoSetSpdUpdtSta": 0,
            "HDA_AutoSetSpdVal": 0,
            "HDA_LFA_WrnSnd": 0,
            "HDA_InfoPUDis1": 0,
            "HDA_TDMRMDclReq": 0,
        }

        result = hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, long_active=False, lat_active=True)

        msg = result[0]
        # LFA_OptUsmSta preserved from camera → 5W cluster knows LFA exists
        assert msg["values"]["LFA_OptUsmSta"] == 2
        # LFA icon active (openpilot lateral active)
        assert msg["values"]["HDA_LFA_SymSta"] == 2
        # Counter properly handled
        assert msg["rx_counter"] == 77

    def test_5w_lfahda_without_camera_data(self):
        """5W: 카메라 원본 없을 때 LFA_OptUsmSta=2 폴백"""
        packer = FakePacker()
        CS = MagicMock()
        CS.lfahda_cluster = None

        result = hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, long_active=False, lat_active=True)

        msg = result[0]
        assert msg["values"]["LFA_OptUsmSta"] == 2
        assert msg["values"]["HDA_OptUsmSta"] == 2
        assert msg["values"]["HDA_LFA_SymSta"] == 2

    def test_5w_no_ccnc_adrv_returns_empty(self):
        """5W 클러스터(CCNC 없음): ADRV_0x161이 없으므로 빈 리스트"""
        packer = FakePacker()
        CS = MagicMock()
        CS.adrv_0x161 = None  # 5W has no CCNC → no ADRV_0x161
        CC = MagicMock()
        CC.latActive = True

        result = hyundaicanfd.create_lfa_icon_non_camera_scc(packer, CS, FakeCAN, CC)
        assert result == [], "5W cluster should return empty (no ADRV_0x161)"

    def test_5w_all_states_combination(self):
        """5W: long/lat 모든 조합 테스트"""
        packer = FakePacker()
        CS = MagicMock()
        CS.lfahda_cluster = {
            "COUNTER": 1,
            "LFA_OptUsmSta": 2,
            "HDA_OptUsmSta": 2,
        }

        expected = {
            (False, False): (0, 0),
            (False, True):  (0, 2),
            (True,  False): (2, 0),
            (True,  True):  (2, 2),
        }

        for (long_a, lat_a), (exp_hda, exp_lfa) in expected.items():
            result = hyundaicanfd.create_lfahda_cluster(packer, CS, FakeCAN, long_a, lat_a)
            msg = result[0]
            assert msg["values"]["HDA_CntrlModSta"] == exp_hda, \
                f"long={long_a},lat={lat_a}: HDA_CntrlModSta expected {exp_hda}"
            assert msg["values"]["HDA_LFA_SymSta"] == exp_lfa, \
                f"long={long_a},lat={lat_a}: HDA_LFA_SymSta expected {exp_lfa}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
