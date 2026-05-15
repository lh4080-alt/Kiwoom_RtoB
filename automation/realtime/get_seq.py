import asyncio 
import websockets
import json
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import socket_url
from api.login import fn_au10001 as get_token

SOCKET_URL = socket_url + '/api/dostk/websocket'

class WebSocketClient:
	def __init__(self, uri):
		self.uri = uri
		self.websocket = None
		self.connected = False
		self.keep_running = True
		self.received_data = None

	# WebSocket 서버에 연결합니다.
	async def connect(self, token):
		try:
			self.websocket = await websockets.connect(self.uri)
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

	# 서버에 메시지를 보냅니다. 연결이 없다면 자동으로 연결합니다.
	async def send_message(self, message, token=None):
		if not self.connected:
			if token:
				await self.connect(token)  # 연결이 끊어졌다면 재연결
		if self.connected:
			# message가 문자열이 아니면 JSON으로 직렬화
			if not isinstance(message, str):
				message = json.dumps(message)

		await self.websocket.send(message)
		print(f'Message sent: {message}')

	# 서버에서 오는 메시지를 수신하여 출력합니다.
	async def receive_messages(self):
		while self.keep_running:
			try:
				# 서버로부터 수신한 메시지를 JSON 형식으로 파싱
				response = json.loads(await self.websocket.recv())

				# 메시지 유형이 LOGIN일 경우 로그인 시도 결과 체크
				if response.get('trnm') == 'LOGIN':
					if response.get('return_code') != 0:
						print('로그인 실패하였습니다. : ', response.get('return_msg'))
						await self.disconnect()
					else:
						print('로그인 성공하였습니다.')

				# 메시지 유형이 PING일 경우 수신값 그대로 송신
				elif response.get('trnm') == 'PING':
					await self.send_message(response)

				if response.get('trnm') != 'PING':
					data = response.get('data')
					if data:
						print(f'실시간 시세 서버 응답 수신(data): {data}')
						self.received_data = data  # 받은 데이터 저장
						self.keep_running = False  # 소켓 중단 플래그 설정
						await self.disconnect()     # 소켓 연결 종료
						return data                 # data를 반환

			except websockets.ConnectionClosed:
				print('Connection closed by the server')
				self.connected = False
				await self.websocket.close()

	# WebSocket 실행
	async def run(self, token):
		await self.connect(token)
		await self.receive_messages()

	# WebSocket 연결 종료
	async def disconnect(self):
		self.keep_running = False
		if self.connected and self.websocket:
			await self.websocket.close()
			self.connected = False
			print('Disconnected from WebSocket server')

async def get_condition_list(token):
	"""조건식 목록을 가져오는 함수"""
	try:
		# WebSocketClient 전역 변수 선언
		websocket_client = WebSocketClient(SOCKET_URL)

		# WebSocket 클라이언트를 백그라운드에서 실행합니다.
		receive_task = asyncio.create_task(websocket_client.run(token))

		# 실시간 항목 등록
		await asyncio.sleep(1)
		await websocket_client.send_message({ 
			'trnm': 'CNSRLST', # TR명
		}, token)

		# 수신 작업이 종료될 때까지 대기
		await receive_task
		
		# 결과 반환 (receive_messages에서 data를 반환하므로)
		return websocket_client.received_data if hasattr(websocket_client, 'received_data') else None
		
	except Exception as e:
		print(f"조건식 목록 가져오기 실패: {e}")
		return None

async def main():
	# WebSocketClient 전역 변수 선언
	websocket_client = WebSocketClient(SOCKET_URL)

	# WebSocket 클라이언트를 백그라운드에서 실행합니다.
	token = await get_token()
	receive_task = asyncio.create_task(websocket_client.run(token))

	# 실시간 항목 등록
	await asyncio.sleep(1)
	await websocket_client.send_message({ 
		'trnm': 'CNSRLST', # TR명
	})

	# 수신 작업이 종료될 때까지 대기
	await receive_task

# asyncio로 프로그램을 실행합니다.
if __name__ == '__main__':
	asyncio.run(main())