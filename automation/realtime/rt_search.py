import asyncio 
import websockets
import json
import random
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import socket_url
from utils.collection_pool import add_to_pool
from utils.get_setting import get_setting
from api.login import fn_au10001 as get_token

class RealTimeSearch:
	def __init__(self, on_connection_closed=None):
		self.socket_url = socket_url + '/api/dostk/websocket'
		self.websocket = None
		self.connected = False
		self.keep_running = True
		self.receive_task = None
		self.on_connection_closed = on_connection_closed  # 연결 종료 시 호출될 콜백 함수
		self.token = None  # 토큰 저장

	async def connect(self, token):
		"""WebSocket 서버에 연결합니다."""
		try:
			self.token = token  # 토큰 저장
			self.websocket = await websockets.connect(self.socket_url)
			self.connected = True
			print("서버와 연결을 시도 중입니다.")

			# 로그인 패킷
			param = {
				'trnm': 'LOGIN',
				'token': token
			}

			print('실시간 시세 서버로 로그인 패킷을 전송합니다.')
			# 웹소켓 연결 시 로그인 정보 전달
			await self.send_message(message=param)

		except Exception as e:
			print(f'Connection error: {e}')
			self.connected = False
			self.websocket = None

	async def send_message(self, message, token=None):
		"""서버에 메시지를 보냅니다. 연결이 없다면 자동으로 연결합니다."""
		if not self.connected:
			if token:
				await self.connect(token)  # 연결이 끊어졌다면 재연결
		if self.connected and self.websocket:
			# message가 문자열이 아니면 JSON으로 직렬화
			if not isinstance(message, str):
				message = json.dumps(message)

			await self.websocket.send(message)
			print(f'Message sent: {message}')

	async def receive_messages(self):
		"""서버에서 오는 메시지를 수신하여 출력합니다."""
		# 조건식 설정 로드 및 역매핑 딕셔너리 준비 (루프 밖에서 한 번만)
		cond_mapping = get_setting('cond', {})
		valid_sequences = {}
		if isinstance(cond_mapping, dict):
			# { "조건식이름": "번호" } 형태를 { "번호": "조건식이름" } 형태로 역매핑
			for condition_name, seq_id in cond_mapping.items():
				# seq_id가 문자열이든 숫자든 문자열로 통일
				seq_str = str(seq_id).strip()
				if seq_str:
					valid_sequences[seq_str] = condition_name
		
		# cond 항목이 없거나 비어있으면 경고 출력 (하지만 동작은 계속)
		if not valid_sequences:
			print("⚠️ 경고: settings.json에 'cond' 항목이 없거나 비어있습니다. 모든 조건식을 허용합니다.")
		
		while self.keep_running and self.connected and self.websocket:
			raw_message = None
			try:
				# 서버로부터 수신한 메시지를 받음
				raw_message = await self.websocket.recv()
				# JSON 형식으로 파싱
				response = json.loads(raw_message)

				# 메시지 유형이 LOGIN일 경우 로그인 시도 결과 체크
				if response.get('trnm') == 'LOGIN':
					if response.get('return_code') != 0:
						print('로그인 실패하였습니다. : ', response.get('return_msg'))
						await self.disconnect()
					else:
						print('로그인 성공하였습니다.')
						print('조건검색 목록조회 패킷을 전송합니다.')
						# 로그인 패킷
						param = {
							'trnm': 'CNSRLST'
						}
						await self.send_message(message=param)

				# 메시지 유형이 PING일 경우 수신값 그대로 송신
				elif response.get('trnm') == 'PING':
					print(f'PING 메시지 수신: {response}')
					await self.send_message(response)

				if response.get('trnm') != 'PING':
					print(f'실시간 시세 서버 응답 수신: {response}')

					if response.get('trnm') == 'REAL' and response.get('data'):
						items = response['data']
						
						if items:
							# response['data'] 리스트를 순회하며 각 item 처리
							for item in items:
								try:
									values = item.get('values', {})
									
									# 조건식 일련번호(seq) 추출
									seq_id = values.get('841')
									if seq_id is None:
										print("⚠️ 경고: 조건식 번호(841)가 없습니다. 이 항목을 건너뜁니다.")
										continue
									
									# seq_id를 문자열로 변환하여 검증
									seq_str = str(seq_id).strip()
									
									# 조건식 번호 검증
									# valid_sequences가 비어있으면 모든 조건식 허용 (경고는 이미 출력됨)
									if valid_sequences and seq_str not in valid_sequences:
										print(f"⚠️ 조건식 번호 {seq_str}는 등록되지 않은 조건식입니다. 건너뜁니다.")
										continue
									
									# 종목코드 추출
									jmcode = values.get('9001')
									if not jmcode:
										print("⚠️ 경고: 종목코드(9001)가 없습니다. 이 항목을 건너뜁니다.")
										continue
									
									# 삽입(I)/삭제(D) 구분 확인 (선택사항, 'I'일 때만 매수 고려)
									insert_type = values.get('843', 'I')
									if insert_type != 'I':
										print(f"⚠️ 삽입 타입이 'I'가 아닙니다 ({insert_type}). 이 항목을 건너뜁니다.")
										continue
									
									# 조건식 이름 가져오기
									condition_name = valid_sequences.get(seq_str) if valid_sequences else None
									
									# 조건검색 매칭 종목을 수집풀에 적재 (매수 안 함)
									asyncio.create_task(add_to_pool(jmcode, condition_name, seq_id=seq_str))
									
								except Exception as e:
									print(f"⚠️ 항목 처리 중 오류 발생: {e}")
									continue
							
							await asyncio.sleep(1)

			except websockets.ConnectionClosed:
				print('Connection closed by the server')
				self.connected = False
				if self.websocket:
					try:
						await self.websocket.close()
					except:
						pass
				
				# 연결 종료 콜백 호출
				if self.on_connection_closed:
					try:
						await self.on_connection_closed()
					except Exception as e:
						print(f'콜백 실행 중 오류: {e}')
				break  # 루프 종료
			
			except json.JSONDecodeError as e:
				print(f'JSON 파싱 오류: {e}')
				print(f'수신한 원본 메시지: {raw_message if raw_message else "수신 실패"}')
				continue  # 다음 메시지 수신 계속
			
			except Exception as e:
				print(f'receive_messages에서 예외 발생: {type(e).__name__}: {e}')
				print(f'연결 상태: connected={self.connected}, websocket={self.websocket is not None}')
				
				# 연결이 끊어진 것으로 보이면 연결 상태 확인
				if self.websocket:
					try:
						# 연결이 살아있는지 확인
						await asyncio.wait_for(self.websocket.ping(), timeout=2)
						print('연결은 유지되고 있습니다. 메시지 수신 계속...')
						continue
					except Exception as ping_e:
						print(f'연결 확인 실패: {ping_e}')
						self.connected = False
						if self.on_connection_closed:
							try:
								await self.on_connection_closed()
							except Exception as callback_e:
								print(f'콜백 실행 중 오류: {callback_e}')
						break  # 루프 종료
				else:
					print('websocket이 None입니다. 루프 종료')
					break  # 루프 종료


	async def disconnect(self):
		"""WebSocket 연결 종료"""
		self.keep_running = False
		if self.connected and self.websocket:
			try:
				await self.websocket.close()
			except Exception as e:
				print(f'WebSocket close error: {e}')
			finally:
				self.connected = False
				self.websocket = None
				print('Disconnected from WebSocket server')

	async def start(self, token):
		"""
		실시간 검색을 시작합니다.
		Returns:
			bool: 성공 여부
		"""
		try:
			# keep_running 플래그를 True로 리셋
			self.keep_running = True
			
			# 이미 웹소켓이 돌고 있다면 종료
			if self.receive_task and not self.receive_task.done():
				self.receive_task.cancel()
				try:
					await self.receive_task
				except asyncio.CancelledError:
					pass
				self.receive_task = None
				await self.disconnect()

			# WebSocket 연결
			await self.connect(token)
			
			# 연결이 성공했는지 확인
			if not self.connected:
				print('WebSocket 연결에 실패했습니다.')
				return False

			# WebSocket 메시지 수신을 백그라운드에서 실행합니다.
			self.receive_task = asyncio.create_task(self.receive_messages())

			seq_value = get_setting('search_seq', '0')
			
			# search_seq가 리스트인지 문자열인지 확인
			if isinstance(seq_value, list):
				seq_list = seq_value
			else:
				# 문자열인 경우 공백이나 쉼표로 분리하여 리스트로 변환
				if isinstance(seq_value, str):
					# 쉼표나 공백으로 구분된 문자열을 리스트로 변환
					seq_list = [s.strip() for s in seq_value.replace(',', ' ').split() if s.strip()]
				else:
					# 숫자인 경우 리스트로 변환
					seq_list = [str(seq_value)]

			# 실시간 항목 등록 (여러 조건식 각각 등록)
			await asyncio.sleep(1)
			for seq in seq_list:
				await self.send_message({ 
					'trnm': 'CNSRREQ', # 서비스명
					'seq': seq, # 조건검색식 일련번호
					'search_type': '1', # 조회타입
					'stex_tp': 'K', # 거래소구분
				}, token)
				print(f'실시간 검색 항목 등록: seq {seq}')
			
			print(f'실시간 검색이 시작되었습니다. 등록된 조건식: {", ".join(seq_list)}')
			return True
			
		except Exception as e:
			print(f'실시간 검색 시작 실패: {e}')
			return False

	async def stop(self):
		"""
		웹소켓 연결을 종료합니다.
		
		Returns:
			bool: 성공 여부
		"""
		try:
			# 이미 웹소켓이 돌고 있다면 종료
			if self.receive_task and not self.receive_task.done():
				self.receive_task.cancel()
				try:
					await self.receive_task
				except asyncio.CancelledError:
					pass
				self.receive_task = None
				await self.disconnect()
			
			print('실시간 검색이 중지되었습니다.')
			return True
			
		except Exception as e:
			print(f'실시간 검색 중지 실패: {e}')
			return False

# 사용 예시
async def main():
	rt_search = RealTimeSearch()
	
	# 실시간 검색 시작
	success = await rt_search.start(get_token())
	if success:
		print("실시간 검색이 성공적으로 시작되었습니다.")
		
		# 10초 후 중지
		await asyncio.sleep(10)
		await rt_search.stop()

if __name__ == '__main__':
	asyncio.run(main())
